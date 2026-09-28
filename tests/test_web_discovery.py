"""Regression cases for one-page discovery and diagnostics on failed runs."""
import json

import pytest

from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore
from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as w


@pytest.mark.parametrize("host,root,expected", [
    ("docs.python.org", "www.python.org", True),
    ("www.python.org", "docs.python.org", True),
    ("DOCS.PYTHON.ORG.", "python.org", True),
    ("docs.example.co.uk", "www.example.co.uk", True),
    ("example.co.uk", "another.co.uk", False),
    ("alice.github.io", "bob.github.io", False),
    ("docs.alice.github.io", "alice.github.io", True),
    ("python.org.evil.com", "python.org", False),
    ("127.0.0.1", "127.0.0.2", False),
    ("localhost", "localhost", True),
    ("", "", False),
])
def test_site_boundaries(host, root, expected):
    assert w._same_site(host, root) is expected


def page(url, html):
    return w.FetchedPage(requested_url=url, final_url=url, html=html,
                         title="Python", text_preview="Python generators use yield.",
                         status=200, content_type="text/html", headers={}, fetch_mode="test")


def test_relevant_long_url_and_sibling_docs_are_not_hidden_by_short_links():
    source = page("https://www.python.org/", '''
        <a href="/dev/">Contributing</a><a href="/jobs/">Jobs</a>
        <a href="https://docs.python.org/">Documentation</a>
        <a href="https://docs.python.org/3/tutorial/classes.html#generators">Generators and yield</a>
        <a href="https://python.org.evil.com/generators">Generators</a>
    ''')
    links = w.extract_related_links(source, "Python generators yield", max_links=2, same_domain_only=True)
    assert [x.anchor for x in links] == ["Generators and yield", "Documentation"]


@pytest.fixture
def lake(tmp_path):
    store = DataStore.init(tmp_path / "warehouse")
    ModelPool(store.root).add(ModelSpec(name="test", api_url="https://model.invalid", api_key="test-secret-key"))
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize("interrupt", [False, True])
def test_failed_or_interrupted_discovery_keeps_completed_steps(lake, monkeypatch, interrupt):
    calls = 0

    def complete(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            # The previous step must be on disk before the next model request.
            trace = next(lake.root.glob("webcrawler_dm_runs/*/trace.jsonl"))
            assert json.loads(trace.read_text())["step"] == 1
            if interrupt:
                raise KeyboardInterrupt()
        raise RuntimeError("provider rejected test-secret-key")

    monkeypatch.setattr(w.llm, "complete", complete)
    agent = w.WebCrawlerDMAgent(w.WebCrawlerDMConfig(model="test", max_steps=2, browser_backend="httpx"))
    try:
        with pytest.raises(KeyboardInterrupt if interrupt else w.WebCrawlerDMError):
            agent.run("Python generators", store=lake, dataset="sources")
    finally:
        agent.close()
    trace = next(lake.root.glob("webcrawler_dm_runs/*/trace.jsonl"))
    text = trace.read_text()
    assert "test-secret-key" not in text
    steps = [json.loads(line) for line in text.splitlines()]
    assert len(steps) == (1 if interrupt else 2)
    assert steps[0]["observation"]["error"] == "RuntimeError: provider rejected [redacted]"
    if not interrupt:
        progress = json.loads(trace.with_name("progress.json").read_text())
        assert progress["status"] == "failed"
        assert progress["trace_path"] == str(trace)


def test_discover_multiple_candidates_but_collect_only_one_page(lake, monkeypatch):
    home = "https://www.python.org/"
    targets = ["https://docs.python.org/3/tutorial/classes.html", "https://docs.python.org/3/reference/expressions.html"]
    source = page(home, ''.join(f'<a href="{url}">Generator {i}</a>' for i, url in enumerate(targets)))
    monkeypatch.setattr(w.WebPageFetcher, "fetch", lambda self, url: source if url == home else page(url, "<p>Generator documentation.</p>"))
    monkeypatch.setattr(w.WebSearchClient, "search", lambda *args: [w.SearchResult(url=home, title="Python", snippet="Python", provider="bing", rank=1)])
    actions = iter([
        {"tool": "search_web", "arguments": {"query": "Python generators"}},
        {"tool": "extract_related_urls", "arguments": {"url": home}},
        {"tool": "submit_resource_urls", "arguments": {"urls": targets}},
    ])
    monkeypatch.setattr(w.llm, "complete", lambda *args, **kwargs: json.dumps(next(actions)))
    agent = w.WebCrawlerDMAgent(w.WebCrawlerDMConfig(
        model="test", max_pages=1, max_links_per_page=50, max_steps=3,
        search_llm_summary=False, browser_backend="httpx", request_delay=0,
    ))
    try:
        result = agent.run("Python generators", store=lake, dataset="sources")
    finally:
        agent.close()
    assert result.selected_urls == targets
    assert result.pages_ingested == result.pages_fetched == 1
    trace = next(lake.root.glob("webcrawler_dm_runs/*/trace.jsonl"))
    steps = [json.loads(line) for line in trace.read_text().splitlines()]
    assert steps[1]["observation"]["count"] == 2
    assert steps[-1]["observation"]["submitted"] is True
