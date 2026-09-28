import json

import pytest

from dataflowwebagent import qa_pipeline, voyager
from dataflowwebagent.agents.Obtainer.datamixer import llm
from dataflowwebagent.agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from dataflowwebagent.agents.Obtainer.datamixer.operators.webpage import PTToSFTQA
from dataflowwebagent.agents.Obtainer.datamixer.store import DataStore
from dataflowwebagent.agents.Obtainer.datamixer.webagents import webcrawler_dm as web
from dataflowwebagent.agents.Obtainer.datamixer.webagents.campaign import LLMQueryExpander, ExpandedQuery


def test_output_preview_needs_no_credentials_or_files(tmp_path, capsys):
    output = tmp_path / "train.jsonl"
    assert voyager.main(["build", "Python QA", "--output", str(output), "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "web_qa"
    assert preview["output"] == str(output)
    assert preview["max_pages"] == 20
    assert not list(tmp_path.iterdir())


def test_generation_receives_audience_language_and_style():
    request = "用中文为初学者生成 Python 生成器问答，每个回答附一个代码示例"
    messages = PTToSFTQA(instruction=request).build_messages([{"content": {"text": "source"}}])
    assert request in messages[-1]["content"]
    assert "supported by its source item" in messages[-1]["content"]


def test_export_validates_pairs_deduplicates_and_separates_sources(tmp_path):
    store = DataStore.init(tmp_path / "warehouse")
    try:
        dataset = store.catalog.add_dataset(name="qa", source="test")
        def record(question, answer, title):
            return {"content": {"messages": [{"role": "user", "content": question},
                                             {"role": "assistant", "content": answer}],
                                "provenance": {"title": title, "source_url": "https://example.org/source"}}}
        store.ingest_records(dataset, [record("Question?", "Answer.", "first"),
                                       record("Question?", "Answer.", "duplicate"),
                                       record("same", "same", "invalid"),
                                       record("empty", None, "invalid")],
                             defaults={"quality_level": "L3"}, decontaminate=False)
        output = tmp_path / "train.jsonl"
        result = qa_pipeline.export_qa(store, "qa", output)
        assert result["rows"] == 1
        assert result["duplicate_pairs_removed"] == 1
        assert result["invalid_rows_removed"] == 2
        assert json.loads(output.read_text()) == {"instruction": "Question?", "input": "", "output": "Answer."}
        source = json.loads(output.with_name(output.name + ".sources.jsonl").read_text())
        assert source["row"] == 1
        assert source["source_url"] == "https://example.org/source"
        with pytest.raises(FileExistsError):
            qa_pipeline.export_qa(store, "qa", output)
    finally:
        store.close()


def test_empty_export_does_not_write_training_file(tmp_path):
    store = DataStore.init(tmp_path / "warehouse")
    try:
        store.catalog.add_dataset(name="empty", source="test")
        with pytest.raises(ValueError, match="No valid QA"):
            qa_pipeline.export_qa(store, "empty", tmp_path / "train.jsonl")
        assert not (tmp_path / "train.jsonl").exists()
    finally:
        store.close()


def test_real_pipeline_to_training_file_with_mocked_web_and_model(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid", model="test"))
    pool.set_default("test")
    monkeypatch.setattr(LLMQueryExpander, "expand", lambda self, query, count: ([ExpandedQuery(query=query)], []))
    monkeypatch.setattr(web.ToolCallingWebAgentKernel, "discover", lambda self, query, tools: (["https://example.org/generators"], [], 1))
    source = ("A generator function uses yield to produce values one at a time. "
              "Calling a generator function returns an iterator. "
              "The function pauses at yield and resumes when the next value is requested. "
              "A generator can avoid constructing a complete list before iteration begins. "
              "This makes generators useful when values can be produced incrementally. "
              "Once exhausted, a generator iterator cannot be restarted.")
    page = web.FetchedPage(requested_url="https://example.org/generators", final_url="https://example.org/generators",
                           html=f"<html><title>Generators</title><body><p>{source}</p></body></html>",
                           title="Generators", text_preview=source, status=200,
                           content_type="text/html", headers={}, fetch_mode="mock")
    monkeypatch.setattr(web.WebPageFetcher, "fetch", lambda self, url: page)
    request = "Generate beginner QA about Python generators, in English."
    calls = []
    def generate(messages):
        calls.append(messages)
        if "Allowed labels:" in messages[-1]["content"]:
            return json.dumps({"results": [{"index": 0, "labels": ["code"], "confidence": 0.99,
                "semantic_signals": [
                    {"type": "generator_behavior", "evidence": "A generator function uses yield to produce values one at a time.", "confidence": 0.99},
                    {"type": "iterator_contract", "evidence": "Calling a generator function returns an iterator.", "confidence": 0.99}]}]})
        assert request in messages[-1]["content"]
        return json.dumps({"results": [{"index": 0, "question": "What does calling a generator function return?",
                                         "answer": "It returns an iterator."}]})
    def post(url, payload, api_key, timeout):
        return {"choices": [{"message": {"content": generate(payload["messages"])}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25}}
    monkeypatch.setattr(llm, "_post", post)
    output = tmp_path / "train.jsonl"
    result = qa_pipeline.run_qa(request, warehouse=warehouse, run=tmp_path / "run", output=output, max_pages=1)
    assert result["status"] == "completed"
    assert result["rows"] == 1
    assert json.loads(output.read_text())["output"] == "It returns an iterator."
    assert len(calls) == 2
    assert (tmp_path / "run" / "report.json").is_file()
    assert result["usage"]["calls"] == 2
    assert result["usage"]["total_tokens"] == 250
    snapshot = json.loads((tmp_path / "run" / "progress.json").read_text())
    assert snapshot["status"] == "completed"
    assert snapshot["pages_collected"] == snapshot["sources_accepted"] == snapshot["qa_candidates"] == 1
    assert voyager.main(["status", "--run", str(tmp_path / "run"), "--json"]) == 0


def test_failed_campaign_does_not_export(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid"))
    pool.set_default("test")
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, "start",
                        lambda *args: {"status": "completed_with_errors", "pipeline": {"ok": False}})
    output = tmp_path / "train.jsonl"
    with pytest.raises(RuntimeError, match="did not complete"):
        qa_pipeline.run_qa("Python QA", warehouse=warehouse, run=tmp_path / "run", output=output)
    assert not output.exists()
    assert json.loads((tmp_path / "run" / "report.json").read_text())["status"] == "failed"


def test_prompt_api_environment_is_sufficient_and_key_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAVOYAGER_MODEL", "my-model")
    monkeypatch.setenv("DATAVOYAGER_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("DATAVOYAGER_API_KEY", "secret-value")
    def start(self, request, config):
        model = ModelPool(self.root).get(config.model)
        assert model.model == "my-model"
        assert model.api_url == "https://model.invalid/v1/chat/completions"
        assert model.resolved_key() == "secret-value"
        assert config.webagent_config["max_pages"] == 1
        assert config.webagent_config["max_links_per_page"] >= 20
        return {"status": "failed"}
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, "start", start)
    with pytest.raises(RuntimeError):
        qa_pipeline.run_qa("Python QA", warehouse=tmp_path / "warehouse", run=tmp_path / "run", output=tmp_path / "train.jsonl", max_pages=1)
    registry = (tmp_path / "warehouse" / "models.json").read_text()
    assert "secret-value" not in registry
    assert "env:DATAVOYAGER_API_KEY" in registry


def test_failed_model_run_retains_usage(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid"))
    pool.set_default("test")
    def start(self, request, config):
        llm.complete(ModelPool(self.root).get(config.model), [], max_retries=0)
    def post(*args):
        raise RuntimeError("LLM HTTP 401")
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, "start", start)
    monkeypatch.setattr(llm, "_post", post)
    with pytest.raises(RuntimeError, match="401"):
        qa_pipeline.run_qa("Python QA", warehouse=warehouse, run=tmp_path / "run", output=tmp_path / "train.jsonl")
    report = json.loads((tmp_path / "run" / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["usage"]["calls"] == report["usage"]["failed_calls"] == 1
    assert not report["usage"]["usage_complete"]
