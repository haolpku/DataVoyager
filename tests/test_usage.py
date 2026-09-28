import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from dataflowwebagent.agents.Obtainer.datamixer import llm
from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from dataflowwebagent.agents.Obtainer.datamixer.telemetry import UsageMeter


@pytest.mark.parametrize("wire,reply", [
    ("openaichat", {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}),
    ("response", {"output_text": "ok", "usage": {"input_tokens": 11, "output_tokens": 7}}),
])
def test_concurrent_usage_is_scoped_and_never_logs_content(tmp_path, monkeypatch, wire, reply):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid", api_key="SECRET", response_format=wire))
    monkeypatch.setattr(llm, "_post", lambda *args: reply)
    with UsageMeter(warehouse, tmp_path) as meter:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(llm.complete, pool.get("test"), [{"role": "user", "content": "PRIVATE PROMPT"}]) for _ in range(12)]
            assert [f.result() for f in futures] == ["ok"] * 12
        # An unrelated warehouse must not accrue to this run.
        assert llm.complete(ModelSpec(name="other", api_url="https://model.invalid", response_format=wire), []) == "ok"
        snapshot = meter.snapshot()
    assert snapshot["calls"] == 12
    assert snapshot["input_tokens"] == 132
    assert snapshot["output_tokens"] == 84
    assert snapshot["usage_complete"]
    log = (tmp_path / "api_calls.jsonl").read_text()
    assert len(log.splitlines()) == 12
    assert "SECRET" not in log and "PRIVATE PROMPT" not in log


def test_retries_and_missing_usage_are_not_reported_as_free(tmp_path, monkeypatch):
    replies = iter([RuntimeError("LLM HTTP 429"), {"choices": [{"message": {"content": "ok"}}]}])
    def post(*args):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(llm, "_post", post)
    monkeypatch.setattr("time.sleep", lambda _: None)
    spec = ModelSpec(name="test", api_url="https://model.invalid", telemetry_key=str(tmp_path.resolve()))
    with UsageMeter(tmp_path, tmp_path) as meter:
        assert llm.complete(spec, [], max_retries=1) == "ok"
        usage = meter.snapshot()
    assert usage["calls"] == 2
    assert usage["failed_calls"] == 1
    assert usage["calls_without_usage"] == 2
    assert not usage["usage_complete"]
    assert usage["cost"] is None


def test_incomplete_responses_still_count_billed_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *args: {"status": "incomplete", "usage": {"input_tokens": 20, "output_tokens": 40}})
    spec = ModelSpec(name="test", api_url="https://model.invalid", response_format="response", telemetry_key=str(tmp_path.resolve()))
    with UsageMeter(tmp_path, tmp_path) as meter:
        with pytest.raises(RuntimeError, match="incomplete"):
            llm.complete(spec, [], max_retries=0)
        usage = meter.snapshot()
    assert usage["failed_calls"] == 1
    assert usage["total_tokens"] == 60
