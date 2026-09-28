"""Opt-in real Codex SDK/CLI transport test against an entirely local Responses API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

import pytest

from dataflowwebagent.chat.agent import codex_turn


@pytest.mark.skipif(os.environ.get("DATAVOYAGER_TEST_SDK") != "1", reason="build codex-runner and set DATAVOYAGER_TEST_SDK=1")
def test_real_sdk_stream_resume_and_provider_usage(tmp_path):
    requests = []
    decision = {"reply": "我会按你的需求规划问答。", "action": "reply", "request": "", "max_pages": 5, "base_run_id": ""}
    class API(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append((self.path, payload))
            text = json.dumps(decision, ensure_ascii=False)
            item = {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
            response = {"id": "resp_test", "object": "response", "status": "completed", "model": "test-model", "output": [item],
                        "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130, "input_tokens_details": {"cached_tokens": 0}}}
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
                {"type": "response.content_part.added", "item_id": "msg_test", "output_index": 0, "content_index": 0,
                 "part": {"type": "output_text", "text": "", "annotations": []}},
                {"type": "response.output_text.delta", "item_id": "msg_test", "output_index": 0, "content_index": 0, "delta": text},
                {"type": "response.output_text.done", "item_id": "msg_test", "output_index": 0, "content_index": 0, "text": text},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    api = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=api.serve_forever, daemon=True)
    thread.start()
    config = {"model": "test-model", "base_url": f"http://127.0.0.1:{api.server_address[1]}/v1", "api_key": "local-fixture-key", "api_format": "responses"}
    events = []
    def emit(event):
        if not event["type"].startswith("_"):
            events.append(event)
    try:
        first = codex_turn({"messages": [{"role": "user", "content": "Python QA"}], "runs": []}, config, tmp_path, emit)
        assert first == decision
        thread_id = next(e["thread_id"] for e in events if e["type"] == "thread.started")
        events.clear()
        second = codex_turn({"thread_id": thread_id, "messages": [{"role": "user", "content": "改成中文"}], "runs": []}, config, tmp_path, emit)
        assert second == decision
        assert all(e["thread_id"] == thread_id for e in events if e["type"] == "thread.started")
        assert next(e["usage"] for e in events if e["type"] == "turn.completed")["input_tokens"] == 200
        assert len(requests) == 2
        assert all(path == "/v1/responses" for path, _ in requests)
        assert "Python QA" in json.dumps(requests[1][1]), "resumed SDK history must retain the first turn"
    finally:
        api.shutdown()
        api.server_close()
        thread.join()
