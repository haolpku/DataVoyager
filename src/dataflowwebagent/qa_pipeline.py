"""A bounded web-to-QA run with a concrete training-file output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import yaml

from .agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from .agents.Obtainer.datamixer.telemetry import UsageMeter
from .qa_progress import QAProgress
from .agents.Obtainer.datamixer.store import DataStore
from .agents.Obtainer.datamixer.webagents import CampaignConfig, WebAgentCampaignRunner


def pipeline_spec(request: str, model: str) -> dict:
    """Use the existing operators without requiring a separate DataFlow install."""
    return {"pipeline": {"name": "web_to_qa", "source": {}, "operators": [
        {"name": "webpage_to_pt", "args": {"engine": "legacy", "min_chars": 300}},
        {"name": "domain_classify", "args": {
            "model": model, "chunk_size": 1, "max_concurrency": 2,
            "max_input_chars": 12000, "max_tokens": 2048}},
        {"name": "topic_quality_filter", "args": {
            "min_semantic_signals": 2, "min_classifier_confidence": 0.8,
            "min_signal_confidence": 0.7},
         "output": {"dataset": "qa_l2", "quality_level": "L2", "stage": "pretrain"}},
        {"name": "pt_to_sft_qa", "args": {
            "model": model, "instruction": request, "chunk_size": 1,
            "max_concurrency": 2, "max_input_chars": 12000, "max_tokens": 4096}},
        {"name": "sft_validate", "args": {"mode": "filter"},
         "output": {"dataset": "qa_l3", "quality_level": "L3", "stage": "sft"}},
    ]}}


def export_qa(store: DataStore, dataset: str, output: Path) -> dict:
    """Export strict two-turn QA as Alpaca rows; keep provenance out of training text."""
    dataset_id = store.catalog.resolve_dataset(dataset)
    if not dataset_id:
        raise ValueError("No QA dataset was produced; inspect the run's campaign report.")
    rows, sources = [], []
    seen = set()
    rejected = duplicates = 0
    for batch in store.catalog.iter_query(dataset_id=dataset_id, where="quality_level = 'L3'"):
        for sample in batch:
            content = store.get_content(sample["cid"])
            messages = content.get("messages") if isinstance(content, dict) else None
            if (not isinstance(messages, list) or len(messages) != 2
                    or not all(isinstance(m, dict) for m in messages)
                    or [m.get("role") for m in messages] != ["user", "assistant"]
                    or not all(isinstance(m.get("content"), str) and m["content"].strip() for m in messages)):
                rejected += 1
                continue
            question, answer = (m["content"].strip() for m in messages)
            if question == answer:
                rejected += 1
                continue
            pair = (question, answer)
            if pair in seen:
                duplicates += 1
                continue
            seen.add(pair)
            rows.append({"instruction": question, "input": "", "output": answer})
            tags = sample.get("tags") or {}
            provenance = content.get("provenance") or {}
            sources.append({"row": len(rows), "sample_id": sample["sample_id"],
                            "source_url": provenance.get("source_url") or tags.get("source_uri"),
                            "title": provenance.get("title"), "tags": tags})
    if not rows:
        raise ValueError("No valid QA pairs survived filtering; no training file was written.")
    source_path = output.with_name(output.name + ".sources.jsonl")
    if output.exists() or source_path.exists():
        raise FileExistsError("Output or source manifest already exists; choose a new --output.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against another run using the same path.
    created = []
    try:
        for path, records in ((source_path, sources), (output, rows)):
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {"output": str(output), "sources": str(source_path), "rows": len(rows),
            "invalid_rows_removed": rejected, "duplicate_pairs_removed": duplicates,
            "format": "alpaca", "factual_verification": "not_performed"}


def resolve_model(warehouse: Path) -> str:
    pool = ModelPool(warehouse)
    api = {key: os.environ.get("DATAVOYAGER_" + key, "").strip() for key in ("MODEL", "BASE_URL", "API_KEY")}
    if any(api.values()):
        if not all(api.values()):
            raise ValueError("Set DATAVOYAGER_MODEL, DATAVOYAGER_BASE_URL and DATAVOYAGER_API_KEY together.")
        from .schema.model_pool import chat_completions_url, responses_url
        wire = os.environ.get("DATAVOYAGER_API_FORMAT", "chat")
        if wire not in {"chat", "responses"}:
            raise ValueError("DATAVOYAGER_API_FORMAT must be chat or responses")
        warehouse.mkdir(parents=True, exist_ok=True)
        model = "voyager_" + uuid.uuid4().hex[:12]
        pool.add(ModelSpec(name=model, model=api["MODEL"],
                           api_url=(responses_url if wire == "responses" else chat_completions_url)(api["BASE_URL"]),
                           api_key="env:DATAVOYAGER_API_KEY", max_tokens=4096,
                           response_format="response" if wire == "responses" else "openaichat"))
    else:
        model = pool.default_name()
    if not model:
        raise ValueError("Set DATAVOYAGER_MODEL, DATAVOYAGER_BASE_URL and DATAVOYAGER_API_KEY.")
    return model


def run_qa(request: str, *, warehouse: Path, run: Path, output: Path,
           max_pages: int = 20, focus: list[str] | None = None) -> dict:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    if output.exists() or output.with_name(output.name + ".sources.jsonl").exists():
        raise FileExistsError("Output already exists; choose a new --output.")
    if (run / "request.json").exists():
        raise FileExistsError("Run already exists; choose a new --run directory.")
    warehouse = warehouse.resolve()
    model = resolve_model(warehouse)
    run.mkdir(parents=True, exist_ok=True)
    with (run / "request.json").open("x", encoding="utf-8") as handle:
        json.dump({"objective": request, "output": str(output), "max_pages": max_pages}, handle, ensure_ascii=False, indent=2)
    store = DataStore.init(warehouse)
    store.close()
    pipeline = run / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump(pipeline_spec(request, model), allow_unicode=True, sort_keys=False))
    prefix = "qa_" + uuid.uuid4().hex[:12]
    config = CampaignConfig(
        model=model, expand_model=model, subquery_count=1, workers=1, task_retries=0,
        dataset=prefix + "_l1", l2_dataset=prefix + "_l2", l3_dataset=prefix + "_l3",
        auto_pipeline=str(pipeline), pipeline_model=model, pipeline_batch_size=2,
        focus_keywords=[request, *(focus or [])],
        webagent_config={"model": model, "browser_backend": "httpx", "max_pages": max_pages,
                         "max_depth": 1, "max_links_per_page": max_pages,
                         "max_steps": 16, "soft_step_limit": 10, "max_search_calls": 3},
    )
    runner = WebAgentCampaignRunner(warehouse)
    try:
        with UsageMeter(warehouse, run) as meter:
            return _execute_qa(runner, request, warehouse, run, output, config, meter)
    finally:
        runner.close()


def _execute_qa(runner, request, warehouse, run, output, config, meter) -> dict:
    progress = QAProgress(warehouse, run, config, meter)
    try:
        progress.start()
        report = runner.start(request, config)
        (run / "campaign.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if report.get("status") != "completed" or not (report.get("pipeline") or {}).get("ok"):
            raise RuntimeError(f"QA pipeline did not complete; inspect {run / 'campaign.json'}")
        store = DataStore.open(warehouse)
        try:
            result = export_qa(store, config.l3_dataset, output)
        finally:
            store.close()
        snapshot = progress.stop("completed")
        result.update({"usage": snapshot["usage"], "elapsed_seconds": snapshot["elapsed_seconds"], "status": "completed", "request": request, "campaign_id": report["run_id"],
                       "source_dataset": config.l2_dataset, "qa_dataset": config.l3_dataset,
                       "run": str(run), "warehouse": str(warehouse)})
        (run / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    except (Exception, KeyboardInterrupt) as exc:
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        snapshot = progress.stop(status)
        (run / "report.json").write_text(json.dumps({"status": status, "error": str(exc), "usage": snapshot["usage"]}, ensure_ascii=False, indent=2) + "\n")
        raise
