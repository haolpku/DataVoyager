import json
from pathlib import Path

import pytest
from qa_fixtures import approved_content, evidence_response

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
    assert preview["mode"] == "dataset_qa"
    assert preview["output"] == str(output)
    assert preview["max_source_rows"] == 50
    assert not list(tmp_path.iterdir())


def test_generation_receives_audience_language_and_style():
    request = "用中文为初学者生成 Python 生成器问答，每个回答附一个代码示例"
    messages = PTToSFTQA(instruction=request).build_messages([{"content": {"text": "source"}}])
    assert request in messages[-1]["content"]
    assert "supported by its source item" in messages[-1]["content"]


def test_hf_qa_rows_use_context_and_case_insensitive_fields():
    finance = {"Question": "What was reported?", "Answer": "(1,577) million",
               "evidence": [{"evidence_text": "PP&E purchases were (1,577) million."}],
               "text": "synthetic question/answer summary"}
    assert qa_pipeline._hf_text(finance) == (
        "PP&E purchases were (1,577) million.", "What was reported?", "(1,577) million")
    medical = {"question": "What is the condition?", "answer": "It affects the skin and hair.",
               "text": "What is the condition?"}
    assert qa_pipeline._hf_text(medical) == (
        "It affects the skin and hair.", "What is the condition?", "It affects the skin and hair.")


def test_explicit_license_request_filters_unknown_and_noncommercial_datasets():
    assert qa_pipeline._requires_clear_license("金融 QA，许可需要明确并允许训练")
    assert qa_pipeline._license_is_clear({"tags": ["license:mit"]})
    assert qa_pipeline._license_is_clear({"tags": ["license:cc-by-4.0"]})
    assert not qa_pipeline._license_is_clear({"tags": ["license:unknown"]})
    assert not qa_pipeline._license_is_clear({"tags": ["license:cc-by-nc-4.0"]})


def test_catalog_search_applies_explicit_training_license_filter(monkeypatch):
    import importlib.util
    from dataflowwebagent.skills.ObtainerCLI import searchagent
    rows = [{"source": "huggingface", "dataset_id": "finance/unknown", "tags": ["license:unknown"]},
            {"source": "huggingface", "dataset_id": "finance/nc", "tags": ["license:cc-by-nc-4.0"]},
            {"source": "huggingface", "dataset_id": "finance/mit", "tags": ["license:mit"]}]
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name in {"datasets", "huggingface_hub"} else None)
    async def search(**kwargs):
        return rows, []
    monkeypatch.setattr(searchagent, "_search_provider_methods", search)
    candidates, report = qa_pipeline._search_dataset_candidates("business training data, license must be clear")
    assert [row["dataset_id"] for row in candidates] == ["finance/mit"]
    assert report["status"] == "found"


def test_finance_and_medical_discovery_uses_only_hand_reviewed_catalog():
    from dataflowwebagent.dataset_catalog import curated_candidates
    finance, finance_report = qa_pipeline._search_dataset_candidates("金融财报问答")
    medical, medical_report = qa_pipeline._search_dataset_candidates("中文医疗高血压问答")
    assert {row["dataset_id"] for row in finance} == {row["dataset_id"] for row in curated_candidates("finance")}
    assert {row["dataset_id"] for row in medical} == {row["dataset_id"] for row in curated_candidates("medical")}
    assert all(row["curated"] and row["curator_note"] for row in finance + medical)
    assert "whalning/Chinese-medical-QA" not in {row["dataset_id"] for row in medical}
    assert finance_report["catalog"] == medical_report["catalog"] == "reviewed_huggingface_candidates"


def test_hf_plural_qa_fields_are_normalized():
    row = {"questions": ["药品甲有什么作用？"], "answers": ["用于缓解相关症状。"]}
    assert qa_pipeline._hf_text(row) == ("用于缓解相关症状。", "药品甲有什么作用？", "用于缓解相关症状。")


def test_collection_downloads_only_user_selected_dataset_ids(tmp_path, monkeypatch):
    from dataflowwebagent.skills.ObtainerCLI import download
    candidates = [{"source": "huggingface", "dataset_id": f"finance/{name}", "tags": ["license:mit"]}
                  for name in ("a", "b", "c")]
    monkeypatch.setattr(qa_pipeline, "_search_dataset_candidates",
                        lambda request: (candidates, {"status": "found", "queries": []}))
    downloaded = []
    def download_manifest(manifest, **kwargs):
        candidate = json.loads(Path(manifest).read_text())["candidates"][0]
        downloaded.append(candidate["dataset_id"])
        return {"results": [{"ok": True, "records_jsonl": "unused.jsonl", "split": "train"}]}
    monkeypatch.setattr(download, "download_manifest", download_manifest)
    monkeypatch.setattr(qa_pipeline, "_model_json", lambda *args, **kwargs: pytest.fail("user selection must be respected"))
    monkeypatch.setattr(qa_pipeline, "_records_from_download", lambda candidate, *args:
                        ([{"content": {"text": candidate["dataset_id"]}}], {"sample_rows_checked": 1, "sample_rows_accepted": 1}))
    rows, report = qa_pipeline._collect_hf_records("finance QA", tmp_path, "test", 20, tmp_path / "downloads",
                                                   selected_dataset_ids=["finance/a", "finance/c"])
    assert downloaded == ["finance/a", "finance/c"]
    assert len(rows) == 2
    assert report["selected_dataset_ids"] == ["finance/a", "finance/c"]


def test_export_validates_pairs_deduplicates_and_separates_sources(tmp_path):
    store = DataStore.init(tmp_path / "warehouse")
    try:
        dataset = store.catalog.add_dataset(name="qa", source="test")
        def record(question, answer, title):
            return {"content": approved_content(question, answer, title)}
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


def test_real_pipeline_to_training_file_from_dataset_without_web_search(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    store = DataStore.init(warehouse)
    store.close()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid", model="test"))
    pool.set_default("test")
    monkeypatch.setattr(qa_pipeline, "_collect_hf_records",
                        lambda *args, **kwargs: ([{"content": {"text": "A generator function uses yield to produce values one at a time. Calling a generator function returns an iterator. The function pauses at yield and resumes when the next value is requested. A generator avoids constructing a complete list before iteration. This makes generators useful when values can be produced incrementally. Once exhausted, a generator iterator cannot be restarted.", "title": "Generators", "source_url": "https://huggingface.co/datasets/example/generators", "provenance": {"source_dataset_id": "example/generators", "source_kind": "huggingface"}}}], {"status": "loaded", "source": "huggingface", "records_loaded": 1}))
    monkeypatch.setattr(web.ToolCallingWebAgentKernel, "discover",
                        lambda *args, **kwargs: pytest.fail("dataset builds must not search the web"))
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
    request = "Generate beginner QA about Python generators, in English."
    calls = []
    def generate(messages):
        calls.append(messages)
        assert request in messages[-1]["content"]
        return json.dumps(evidence_response(messages))
    def post(url, payload, api_key, timeout):
        return {"choices": [{"message": {"content": generate(payload["messages"])}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25}}
    monkeypatch.setattr(llm, "_post", post)
    output = tmp_path / "train.jsonl"
    result = qa_pipeline.run_qa(request, warehouse=warehouse, run=tmp_path / "run", output=output, max_pages=1)
    assert result["status"] == "completed"
    assert result["rows"] == 1
    assert json.loads(output.read_text())["output"] == "It returns an iterator."
    assert len(calls) == 3
    assert (tmp_path / "run" / "report.json").is_file()
    assert result["usage"]["calls"] == 3
    assert result["usage"]["total_tokens"] == 375
    snapshot = json.loads((tmp_path / "run" / "progress.json").read_text())
    assert snapshot["status"] == "completed"
    assert snapshot["pages_collected"] == snapshot["sources_accepted"] == snapshot["qa_candidates"] == 1
    assert result["source_acquisition"]["datasets"]["source"] == "huggingface"
    assert voyager.main(["status", "--run", str(tmp_path / "run"), "--json"]) == 0


def test_failed_campaign_does_not_export(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid"))
    pool.set_default("test")
    monkeypatch.setattr(qa_pipeline, "_collect_hf_records",
                        lambda *args, **kwargs: ([{"content": {"text": "source"}}], {"status": "loaded", "records_loaded": 1}))
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, "start",
                        lambda *args, **kwargs: {"status": "completed_with_errors", "pipeline": {"ok": False}})
    output = tmp_path / "train.jsonl"
    with pytest.raises(RuntimeError, match="did not complete"):
        qa_pipeline.run_qa("Python QA", warehouse=warehouse, run=tmp_path / "run", output=output)
    assert not output.exists()
    assert json.loads((tmp_path / "run" / "report.json").read_text())["status"] == "failed"


def test_no_dataset_match_returns_shortfall_without_web_fallback(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    DataStore.init(warehouse).close()
    pool = ModelPool(warehouse)
    pool.add(ModelSpec(name="test", api_url="https://model.invalid")); pool.set_default("test")
    monkeypatch.setattr(qa_pipeline, "_collect_hf_records",
                        lambda *args, **kwargs: ([], {"status": "no_results", "records_loaded": 0}))
    monkeypatch.setattr(qa_pipeline.WebAgentCampaignRunner, "start",
                        lambda *args, **kwargs: pytest.fail("no web campaign should run without dataset rows"))
    result = qa_pipeline.run_qa("Create 5 QA pairs about Python", warehouse=warehouse,
                                run=tmp_path / "run", output=tmp_path / "qa.jsonl", target_rows=5)
    assert result["status"] == "needs_confirmation"
    assert result["stop_reason"] == "no_suitable_dataset"
    assert result["rows"] == 0 and not (tmp_path / "qa.jsonl").exists()


def test_prompt_api_environment_is_sufficient_and_key_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAVOYAGER_MODEL", "my-model")
    monkeypatch.setenv("DATAVOYAGER_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("DATAVOYAGER_API_KEY", "secret-value")
    monkeypatch.setattr(qa_pipeline, "_collect_hf_records",
                        lambda *args, **kwargs: ([{"content": {"text": "source"}}], {"status": "loaded", "records_loaded": 1}))
    def start(self, request, config, **kwargs):
        model = ModelPool(self.root).get(config.model)
        assert model.model == "my-model"
        assert model.api_url == "https://model.invalid/v1/chat/completions"
        assert model.resolved_key() == "secret-value"
        assert kwargs["collect_web"] is False
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
    monkeypatch.setattr(qa_pipeline, "_collect_hf_records",
                        lambda *args, **kwargs: ([{"content": {"text": "source"}}], {"status": "loaded", "records_loaded": 1}))
    def start(self, request, config, **kwargs):
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
