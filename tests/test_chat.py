from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dataflowwebagent.chat.server import ChatServer
from dataflowwebagent.chat.workspace import Workspace, write_json, preview
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for key in ("MODEL", "BASE_URL", "API_KEY", "API_FORMAT"):
        monkeypatch.delenv("DATAVOYAGER_" + key, raising=False)


def wait_until(condition, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.02)
    raise AssertionError("background operation did not finish")


@contextmanager
def server_for(app):
    server = ChatServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    def request(path, data=None, headers=None):
        request_headers = {"X-DataVoyager-Token": server.token, "Content-Type": "application/json", **(headers or {})}
        req = Request(base + path, headers=request_headers, data=json.dumps(data).encode() if data is not None else None)
        with urlopen(req, timeout=3) as response:
            body = response.read()
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body
    try:
        yield request
    finally:
        app.close()
        server.shutdown()
        server.server_close()
        thread.join()


def configure(app, base="https://model.invalid/v1"):
    app.configure({"base_url": base, "model": "test-model", "api_key": "SECRET-TEST-KEY", "api_format": "chat"})


def test_http_origin_host_token_and_config_secrecy(tmp_path):
    app = Workspace(tmp_path)
    with server_for(app) as request:
        assert b"DataVoyager" in request("/")
        with pytest.raises(HTTPError) as error:
            request("/api/sessions", {}, {"X-DataVoyager-Token": "wrong"})
        assert error.value.code == 403
        for headers in ({"Origin": "https://attacker.invalid"}, {"Host": "attacker.invalid"}):
            with pytest.raises(HTTPError) as error:
                request("/api/bootstrap", headers=headers)
            assert error.value.code == 403
        configure(app)
        boot = request("/api/bootstrap")
        assert boot["config"]["configured"]
        assert "SECRET-TEST-KEY" not in json.dumps(boot)
        assert "SECRET-TEST-KEY" not in (tmp_path / "settings.json").read_text()
        sid = request("/api/sessions", {})["id"]
        assert request("/api/sessions/" + sid)["messages"] == []
        with pytest.raises(HTTPError):
            request("/api/sessions/../settings")
        with pytest.raises(HTTPError):
            request(f"/api/sessions/{sid}/runs/0000000000000000/download/qa")
    restored = Workspace(tmp_path)
    assert not restored.public_config()["configured"]


def test_multiturn_state_survives_restart_and_usage_is_separate(tmp_path):
    seen = []
    def agent(context, config, directory, emit):
        seen.append(context)
        emit({"type": "thread.started", "thread_id": "thread-1"})
        emit({"type": "turn.completed", "usage": {"input_tokens": 100 * len(seen), "output_tokens": 20 * len(seen)}})
        return {"reply": "我记住了：中文、初学者。", "action": "reply"}
    app = Workspace(tmp_path, agent=agent)
    configure(app)
    sid = app.create()["id"]
    app.send(sid, "做 Python 问答，用中文")
    wait_until(lambda: not app.snapshot(sid)["busy"])
    app.close()
    app = Workspace(tmp_path, agent=agent)
    configure(app)
    app.send(sid, "面向初学者")
    wait_until(lambda: not app.snapshot(sid)["busy"])
    assert seen[1]["thread_id"] == "thread-1"
    assert "做 Python 问答，用中文" in json.dumps(seen[1], ensure_ascii=False)
    state = app.snapshot(sid)
    assert len(state["agent_turns"]) == 2
    assert all(t["usage"]["input_tokens"] == 100 for t in state["agent_turns"])
    assert state["runs"] == []
    app.close()


def seed_version(app, sid):
    rid = "0123456789abcdef"
    root = app.session_dir(sid) / "versions" / rid
    store = DataStore.init(root / "warehouse")
    dataset = store.catalog.add_dataset(name="sources", source="test")
    store.ingest_records(dataset, [{"content": {"text": "A generator function returns an iterator. Execution starts when next() is called.", "source_url": "https://example.org/generators", "title": "Generators"},
                                    "tags": {"source_uri": "https://example.org/generators"}}], defaults={"quality_level": "L2"})
    store.close()
    write_json(root / "run" / "progress.json", {"status": "completed", "sources_accepted": 1, "qa_candidates": 1})
    (root / "qa.jsonl").write_text(json.dumps({"instruction": "Old question?", "input": "", "output": "Old answer."}) + "\n")
    state = app.chats[sid]
    state["runs"].append({"id": rid, "version": 1, "action": "build", "request": "Python generators for beginners", "status": "completed", "max_pages": 5})
    app._save(state)
    return rid, root


def test_real_revision_subprocess_preserves_old_version_and_exports_sources(tmp_path):
    captured = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            body = json.dumps({"data": [{"id": "test-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            content = json.dumps({"results": [{"index": 0, "question": "调用生成器函数会立即执行吗？", "answer": "不会。调用时返回迭代器，调用 next() 才开始执行。"}]}, ensure_ascii=False)
            body = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 80, "completion_tokens": 30}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    rid = "0123456789abcdef"
    def agent(context, config, directory, emit):
        emit({"type": "thread.started", "thread_id": "revision-thread"})
        emit({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}})
        return {"reply": "我会复用原资料，生成中文的新版本。", "action": "revise", "request": "Python generators for beginners，用中文解释容易误解的点", "max_pages": 5, "base_run_id": rid}
    app = Workspace(tmp_path, agent=agent)
    configure(app, f"http://127.0.0.1:{model.server_address[1]}/v1")
    sid = app.create()["id"]
    _, old = seed_version(app, sid)
    before = (old / "qa.jsonl").read_bytes(), (old / "warehouse" / "catalog.db").read_bytes()
    try:
        with server_for(app) as request:
            request(f"/api/sessions/{sid}/messages", {"message": "改成中文，讲容易误解的点"})
            wait_until(lambda: len(app.snapshot(sid)["runs"]) == 2)
            wait_until(lambda: app.snapshot(sid)["runs"][-1]["status"] not in {"queued", "running"}, timeout=15)
            run = app.snapshot(sid)["runs"][-1]
            assert run["status"] == "completed", run
            assert run["report"]["reused_sources"] == 1
            assert run["report"]["usage"]["calls"] == 1
            assert run["report"]["usage"]["total_tokens"] == 110
            assert run["progress"]["pages_collected"] == 0
            assert run["samples"][0]["source_url"] == "https://example.org/generators"
            assert "用中文解释容易误解的点" in captured[0]["messages"][-1]["content"]
            downloaded = request(f"/api/sessions/{sid}/runs/{run['id']}/download/qa")
            assert json.loads(downloaded)["instruction"] == "调用生成器函数会立即执行吗？"
            assert before == ((old / "qa.jsonl").read_bytes(), (old / "warehouse" / "catalog.db").read_bytes())
    finally:
        model.shutdown()
        model.server_close()
        model_thread.join()


def test_failed_agent_never_dispatches_and_redacts_provider_error(tmp_path):
    def agent(context, config, directory, emit):
        emit({"type": "turn.completed", "usage": {"input_tokens": 25, "output_tokens": 3}})
        raise RuntimeError("upstream failed with SECRET-TEST-KEY")
    app = Workspace(tmp_path, agent=agent)
    configure(app)
    sid = app.create()["id"]
    app.send(sid, "build")
    wait_until(lambda: not app.snapshot(sid)["busy"])
    state = app.snapshot(sid)
    assert not state["runs"]
    assert state["agent_turns"][0]["status"] == "failed"
    assert state["agent_turns"][0]["usage"]["input_tokens"] == 25
    assert "SECRET-TEST-KEY" not in json.dumps(state)


def test_cancel_terminates_actual_worker_and_preserves_job(tmp_path, monkeypatch):
    import dataflowwebagent.chat.workspace as module
    original = subprocess.Popen
    processes = []
    def spawn(*args, **kwargs):
        p = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(p)
        return p
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    app = Workspace(tmp_path)
    configure(app)
    sid = app.create()["id"]
    with app.lock:
        app._launch(app.chats[sid], {"action": "build", "request": "Python QA", "max_pages": 1}, app.config)
    wait_until(lambda: processes and app.snapshot(sid)["runs"][0]["status"] == "running")
    rid = app.snapshot(sid)["runs"][0]["id"]
    app.cancel(sid, rid)
    wait_until(lambda: processes[0].poll() is not None)
    assert app.snapshot(sid)["runs"][0]["status"] == "cancelled"
    assert app.snapshot(sid)["runs"][0]["progress"]["stage"] == "cancelled"
    assert (app.session_dir(sid) / "versions" / rid / "job.json").is_file()
    app.close()


def test_model_cannot_revise_other_sessions(tmp_path):
    app = Workspace(tmp_path)
    configure(app)
    a, b = app.create()["id"], app.create()["id"]
    rid, _ = seed_version(app, a)
    with app.lock, pytest.raises(ValueError, match="找不到"):
        app._launch(app.chats[b], {"action": "revise", "request": "Chinese QA", "max_pages": 5, "base_run_id": rid}, app.config)
    assert not app.snapshot(b)["runs"]


def test_duplicate_turns_rejected_and_interrupted_state_is_recovered(tmp_path):
    app = Workspace(tmp_path)
    configure(app)
    sid = app.create()["id"]
    app.chats[sid]["busy"] = True
    app.chats[sid]["agent_turns"].append({"status": "running", "usage": None})
    app._save(app.chats[sid])
    with pytest.raises(ValueError, match="正在回复"):
        app.send(sid, "repeat")
    restored = Workspace(tmp_path)
    snapshot = restored.snapshot(sid)
    assert not snapshot["busy"]
    assert snapshot["agent_turns"][0]["status"] == "interrupted"
