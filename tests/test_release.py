"""Network-free regression checks for the alpha entry point and runtime fixes."""
import importlib.util
import json
import socket
from pathlib import Path

import httpx
import pytest

from dataflowwebagent import badcase_pipeline, voyager
from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore
from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as w
from dataflowwebagent.agents.Obtainer.datamixer.webagents.campaign import (
    CampaignConfig, ExpandedQuery, WebAgentCampaignRunner,
)
from dataflowwebagent.skills.ObtainerCLI.dataset_acquisition_agent import _ensure_webagent_model, _worker_env


def test_build_dry_run_preserves_request_without_writes(tmp_path, capsys):
    run = tmp_path / "run"
    assert voyager.main(["build", "构建 Python 修复数据", "--focus", "type repair",
                         "--target-datasets", "3", "--run", str(run), "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["request"]["objective"] == "构建 Python 修复数据"
    assert payload["request"]["focus_keywords"] == ["type repair"]
    assert payload["request"]["target_datasets"] == 3
    assert not run.exists()


@pytest.mark.parametrize("args", [["build", " "], ["build", "data", "--target-datasets", "0"]])
def test_build_rejects_invalid_requests(args):
    with pytest.raises(SystemExit) as exc:
        voyager.main(args)
    assert exc.value.code == 2


def test_build_routes_to_acquisition_and_prevents_overwrite(tmp_path, monkeypatch):
    run = tmp_path / "run"
    captured = []
    monkeypatch.setattr(badcase_pipeline, "main", lambda args: captured.append(args) or 0)
    args = ["build", "Collect repair examples", "--run", str(run), "--warehouse", str(tmp_path / "lake")]
    assert voyager.main(args) == 0
    request = run / "request.json"
    assert json.loads(request.read_text())["objective"] == "Collect repair examples"
    assert captured[0][captured[0].index("--badcase") + 1] == str(request)
    with pytest.raises(SystemExit):
        voyager.main(args)
    assert len(captured) == 1


def test_dm_passthrough_initializes_store(tmp_path, capsys):
    assert voyager.main(["dm", "--root", str(tmp_path), "init", "--json"]) == 0
    assert (tmp_path / "datamixer.toml").is_file()


def test_badcase_forwards_full_report(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"domain": "code", "failure_examples": [{"answer": "details"}]}))
    assert badcase_pipeline.main(["--badcase", str(report), "--warehouse", str(tmp_path / "lake"),
                                  "--run", str(tmp_path / "run"), "--dry-run"]) == 0
    command = json.loads(capsys.readouterr().out)["command"]
    assert command[command.index("--analysis-report") + 1] == str(report.resolve())


@pytest.fixture
def fetcher(monkeypatch):
    def resolve(host, *args, **kwargs):
        ip = host if host in {"127.0.0.1", "10.0.0.1"} else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]
    monkeypatch.setattr(w.socket, "getaddrinfo", resolve)
    obj = w.WebPageFetcher(w.WebCrawlerDMConfig(
        browser_backend="auto", use_env_proxy=False, respect_robots_txt=False,
        request_delay=0, max_retries=0))
    obj._http.close()
    yield obj
    obj.close()


@pytest.mark.parametrize("target", ["http://127.0.0.1/private", "http://10.0.0.1/private", "file:///etc/passwd"])
def test_redirect_rejected_before_connection_or_browser_fallback(fetcher, monkeypatch, target):
    visited = []
    def handle(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": target})
    fetcher._http = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    monkeypatch.setattr(fetcher, "_fetch_playwright", lambda url: pytest.fail("unsafe browser fallback"))
    with pytest.raises(w.UnsafeURLError):
        fetcher.fetch("https://public.example/start")
    assert visited == ["https://public.example/start"]


def test_public_redirect_preserves_query_and_succeeds(fetcher):
    visited = []
    def handle(request):
        visited.append(str(request.url))
        if len(visited) == 1:
            return httpx.Response(302, headers={"location": "/article?ref=required"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>article</p>")
    fetcher._http = httpx.Client(transport=httpx.MockTransport(handle))
    page = fetcher.fetch("https://public.example/start")
    assert visited[-1] == "https://public.example/article?ref=required"
    assert page.status == 200


def test_robots_redirect_cannot_access_private_network(fetcher):
    fetcher.config.respect_robots_txt = True
    visited = []
    def handle(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    fetcher._http = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(w.UnsafeURLError):
        fetcher.fetch("https://public.example/start")
    assert visited == ["https://public.example/robots.txt"]


def test_redirect_loop_is_bounded(fetcher):
    visited = []
    def handle(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "/loop"})
    fetcher._http = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(w.WebCrawlerDMError, match="too many HTTP redirects"):
        fetcher._fetch_http("https://public.example/start")
    assert len(visited) == 11


def test_failed_crawl_marks_campaign_failed(tmp_path, monkeypatch):
    store = DataStore.init(tmp_path)
    store.close()
    class Search:
        def primary_provider(self):
            return "mock"
    class Fetcher:
        def fetch(self, url):
            raise RuntimeError("simulated HTTP 503")
        def browser_status(self):
            return {}
    class Expander:
        def expand(self, query, count):
            return [ExpandedQuery(query=query)], []
    monkeypatch.setattr(w.ToolCallingWebAgentKernel, "discover",
                        lambda *args: (["https://public.example/start"], [], 1))
    def execute(task, ctx):
        db = DataStore.open(tmp_path)
        try:
            agent = w.WebCrawlerDMAgent(search_client=Search(), fetcher=Fetcher())
            return agent.run(task["query"], store=db, dataset="review_l1").to_dict()
        finally:
            db.close()
    runner = WebAgentCampaignRunner(tmp_path, task_executor=execute,
                                    expander_factory=lambda *args: Expander())
    try:
        report = runner.start("test", CampaignConfig(subquery_count=1, workers=1, task_retries=0))
        assert report["status"] == "completed_with_errors"
        assert report["tasks"][0]["status"] == "failed"
        progress = next((tmp_path / "webcrawler_dm_runs").glob("*/progress.json"))
        assert json.loads(progress.read_text())["status"] == "failed"
    finally:
        runner.close()


@pytest.mark.parametrize("standard_env", [True, False])
def test_managed_keys_stay_in_environment(tmp_path, monkeypatch, standard_env):
    key = "not-a-real-key-for-regression"
    if standard_env:
        monkeypatch.setenv("DATAFLOWWEBAGENT_API_KEY", key)
    else:
        monkeypatch.delenv("DATAFLOWWEBAGENT_API_KEY", raising=False)
    _ensure_webagent_model(tmp_path, {"base_url": "https://model.example/v1", "api_key": key},
                           {"webagent_model": "test", "resolved_model": "test"})
    serialized = (tmp_path / "models.json").read_text()
    assert key not in serialized
    spec = ModelPool(tmp_path).get("test")
    assert spec.api_key.startswith("env:")
    assert spec.resolved_key() == key
    assert _worker_env()[spec.api_key[4:]] == key


def test_offline_demo_materializes_l1_l2_and_lineage(tmp_path):
    module_path = Path(__file__).parents[1] / "examples" / "offline_demo.py"
    spec = importlib.util.spec_from_file_location("offline_demo", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_demo(tmp_path)
    assert report["counts"] == {"offline_l1": 2, "offline_l2": 2}
    assert list((tmp_path / "lineage").glob("*.json"))
