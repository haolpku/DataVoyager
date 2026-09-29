"""A bounded web-to-QA run with a concrete training-file output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
import math
import re

import yaml

from .agents.Obtainer.datamixer.models import ModelPool, ModelSpec
from .agents.Obtainer.datamixer.telemetry import UsageMeter
from .qa_progress import QAProgress
from .qa_quantity import resolve_target, quantity_result
from .agents.Obtainer.datamixer.store import DataStore
from .agents.Obtainer.datamixer.webagents import CampaignConfig, WebAgentCampaignRunner


def pipeline_spec(request: str, model: str, stop_after: str = "qa") -> dict:
    """Use the existing operators without requiring a separate DataFlow install."""
    spec = {"pipeline": {"name": "web_to_evidence_qa", "source": {}, "operators": [
        {"name": "evidence_prepare"},
        {"name": "evidence_select", "args": {"model": model, "instruction": request, "max_tokens": 4096},
         "output": {"dataset": "qa_l2", "quality_level": "L2", "stage": "pretrain"}},
        {"name": "evidence_qa_generate", "args": {"model": model, "instruction": request, "max_tokens": 4096}},
        {"name": "evidence_qa_review", "args": {"model": model, "instruction": request, "max_tokens": 4096},
         "output": {"dataset": "qa_l3", "quality_level": "L3", "stage": "sft"}},
    ]}}
    if stop_after == "corpus":
        spec["pipeline"]["operators"] = spec["pipeline"]["operators"][:2]
    return spec


def qa_records(store: DataStore, dataset: str) -> tuple[list, list, dict]:
    """Count and export through the same validation and question deduplication."""
    dataset_id = store.catalog.resolve_dataset(dataset)
    if not dataset_id:
        return [], [], {"invalid_rows_removed": 0, "duplicate_pairs_removed": 0}
    from .qa_artifacts import stage_records
    held = {c['candidate_id'] for c in stage_records(store.root, 'candidates') if c.get('status') != 'source_supported'}
    rows, sources = [], []
    seen, grams = set(), []
    rejected = duplicates = unreviewed = 0
    samples = [s for batch in store.catalog.iter_query(dataset_id=dataset_id, where="quality_level = 'L3'") for s in batch]
    for sample in sorted(samples, key=lambda s: (s["created_at"], s["sample_id"])):
        content = store.get_content(sample["cid"])
        if not isinstance(content, dict) or not content.get("review_complete"):
            unreviewed += 1
            continue
        for candidate in content.get("qa_candidates", []):
            if candidate.get("status") != "source_supported" or candidate.get("candidate_id") in held:
                unreviewed += 1
                continue
            question, answer = candidate.get("question"), candidate.get("answer")
            if not all(isinstance(x, str) and x.strip() for x in (question, answer)) or question == answer:
                rejected += 1
                continue
            question, answer = question.strip(), answer.strip()
            key = re.sub(r"\W+", "", question).casefold()
            if not key:
                rejected += 1
                continue
            # Conservative lexical near-duplicate check; preserve different numeric conditions.
            shingles = {key[i:i + 3] for i in range(len(key) - 2)} if len(key) >= 20 else set()
            numbers = re.findall(r"\d+(?:\.\d+)?", question)
            near = any(numbers == n and shingles and g and len(shingles & g) / len(shingles | g) >= .9 for g, n in grams)
            if key in seen or near:
                duplicates += 1
                continue
            seen.add(key)
            grams.append((shingles, numbers))
            rows.append({"instruction": question, "input": "", "output": answer})
            sources.append({"row": len(rows), "sample_id": sample["sample_id"],
                            "candidate_id": candidate["candidate_id"], "source_url": candidate.get("source_url"),
                            "title": candidate.get("title"), "segment": candidate.get("segment"),
                            "claims": candidate.get("claims"), "review": candidate.get("review"),
                            "verification": "model_source_review_not_expert_certification"})
    return rows, sources, {"invalid_rows_removed": rejected, "duplicate_pairs_removed": duplicates,
                           "duplicate_questions_removed": duplicates, "unverified_rows_removed": unreviewed}


def export_qa(store: DataStore, dataset: str, output: Path, *, target_rows: int | None = None) -> dict:
    """Export strict two-turn QA as Alpaca rows; keep provenance out of training text."""
    rows, sources, metrics = qa_records(store, dataset)
    eligible = len(rows)
    if target_rows:
        rows, sources = rows[:target_rows], sources[:target_rows]
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
            **metrics, "eligible_rows": eligible,
            "format": "alpaca", "factual_verification": "model_source_review"}


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
           max_pages: int = 20, focus: list[str] | None = None,
           target_rows: int | None = None, max_rounds: int = 5,
           base_warehouse: Path | None = None, stop_after: str = "qa") -> dict:
    if stop_after not in {"raw", "corpus", "qa"}:
        raise ValueError("stop_after must be raw, corpus or qa")
    if base_warehouse and stop_after != "qa":
        raise ValueError("Source-only runs must be new collections")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    target_rows = resolve_target(request, target_rows)
    if type(max_rounds) is not int or not 1 <= max_rounds <= 10:
        raise ValueError("max_rounds must be 1–10")
    if output.exists() or output.with_name(output.name + ".sources.jsonl").exists():
        raise FileExistsError("Output already exists; choose a new --output.")
    if (run / "request.json").exists():
        raise FileExistsError("Run already exists; choose a new --run directory.")
    warehouse = warehouse.resolve()
    model = resolve_model(warehouse)
    run.mkdir(parents=True, exist_ok=True)
    with (run / "request.json").open("x", encoding="utf-8") as handle:
        json.dump({"objective": request, "output": str(output), "max_pages": max_pages,
                   "target_rows": target_rows if stop_after == "qa" else None, "max_rounds": max_rounds, "stop_after": stop_after}, handle, ensure_ascii=False, indent=2)
    store = DataStore.init(warehouse)
    store.close()
    pipeline = run / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump(pipeline_spec(request, model, stop_after), allow_unicode=True, sort_keys=False))
    prefix = "qa_" + uuid.uuid4().hex[:12]
    config = CampaignConfig(
        model=model, expand_model=model, subquery_count=1, workers=1, task_retries=0,
        dataset=prefix + "_l1", l2_dataset=prefix + "_l2", l3_dataset=prefix + "_l3",
        auto_pipeline=str(pipeline) if stop_after != "raw" else "", pipeline_model=model, pipeline_batch_size=2,
        focus_keywords=[request, *(focus or [])],
        webagent_config={"model": model, "browser_backend": "httpx", "max_pages": max_pages,
                         # Discovery needs alternatives even when collecting one page.
                         "max_depth": 1, "max_links_per_page": 50,
                         "max_steps": 16, "soft_step_limit": 10, "max_search_calls": 3},
    )
    runner = WebAgentCampaignRunner(warehouse)
    try:
        if base_warehouse:
            _copy_previous(base_warehouse, warehouse, config)
        with UsageMeter(warehouse, run) as meter:
            if stop_after != "qa":
                return _execute_sources(runner, request, warehouse, run, config, meter, stop_after)
            return _execute_qa(runner, request, warehouse, run, output, config, meter,
                               target_rows=target_rows, max_rounds=max_rounds, max_pages=max_pages)
    finally:
        runner.close()


def _copy_previous(base: Path, warehouse: Path, config):
    """Copy into a new version; never mutate the previous warehouse."""
    from .chat.worker import read_sources
    store = DataStore.open(warehouse)
    try:
        for level, dataset in (("L1", config.dataset), ("L2", config.l2_dataset), ("L3", config.l3_dataset)):
            did = store.catalog.add_dataset(name=dataset, source="continued_version")
            store.ingest_records(did, read_sources(base, level=level),
                                 defaults={"quality_level": level}, decontaminate=False)
    finally:
        store.close()
    from .qa_artifacts import stage_records, save_stage, identity
    for stage in ('source-review', 'candidates'):
        for record in stage_records(base, stage):
            save_stage(warehouse, stage, record.get('candidate_id') or identity(record), record)


def _execute_qa(runner, request, warehouse, run, output, config, meter, *,
                target_rows=None, max_rounds=5, max_pages=20) -> dict:
    progress = QAProgress(warehouse, run, config, meter)
    progress.quantity = {"target_rows": target_rows, "generated_rows": 0, "round": 0,
                         "page_budget": max_pages, "page_budget_allocated": 0}
    try:
        progress.start()
        rounds, allocated, stagnant = [], 0, 0
        reason = "round_limit"
        store = DataStore.open(warehouse)
        try:
            rows, sources, _ = qa_records(store, config.l3_dataset)
        finally:
            store.close()
        for index in range(max_rounds if target_rows else 1):
            if target_rows and len(rows) >= target_rows:
                break
            remaining = max_pages - allocated
            if remaining <= 0:
                reason = "page_budget_exhausted"
                break
            budget = remaining if not target_rows else min(remaining, target_rows - len(rows),
                       20 if index == 0 else math.ceil(remaining / (max_rounds - index)))
            allocated += budget
            config.webagent_config["max_pages"] = budget
            progress.quantity.update(round=index + 1, generated_rows=len(rows), page_budget_allocated=allocated)
            query = request
            if index or rows:
                query += (f"\n补充采集第 {index + 1} 轮：目前已有 {len(rows)} 条不同问题，目标 {target_rows} 条。"
                          "保持原主题、语言和来源限制，查找其他相关正文页面，不要扩大主题或重复已覆盖问题。"
                          "\n已使用的资料 URL（仅作去重参考）：" + json.dumps([s.get("source_url") for s in sources][-50:]))
            report = runner.start(query, config)
            (run / f"campaign-{index + 1}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            (run / "campaign.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            pipeline = report.get("pipeline") or {}
            empty_only = (target_rows and str(pipeline.get("error", "")).startswith("no records materialized")
                          and not any(s.get("failed") for s in pipeline.get("stages", []))
                          and report.get("queue", {}).get("failed") == 0
                          and not report.get("queue", {}).get("pending")
                          and not report.get("queue", {}).get("running")
                          and pipeline.get("status") == "completed")
            if not empty_only and (report.get("status") != "completed" or not pipeline.get("ok")):
                raise RuntimeError(f"QA pipeline did not complete; inspect {run / 'campaign.json'}")
            before = len(rows)
            store = DataStore.open(warehouse)
            try:
                rows, sources, _ = qa_records(store, config.l3_dataset)
            finally:
                store.close()
            added = len(rows) - before
            rounds.append({"round": index + 1, "page_budget": budget, "new_rows": added,
                           "rows": len(rows), "campaign_id": report["run_id"]})
            progress.quantity.update(generated_rows=min(len(rows), target_rows or len(rows)), rounds=rounds)
            stagnant = stagnant + 1 if added == 0 else 0
            if stagnant >= 2:
                reason = "no_new_questions"
                break
        if allocated >= max_pages:
            reason = "page_budget_exhausted"
        store = DataStore.open(warehouse)
        try:
            result = export_qa(store, config.l3_dataset, output, target_rows=target_rows) if rows else {
                "rows": 0, "output": None, "sources": None, "factual_verification": "model_source_review"}
        finally:
            store.close()
        if not rows and not target_rows:
            raise ValueError("No valid QA pairs survived filtering")
        result.update(quantity_result(result["rows"], target_rows, reason))
        progress.quantity.update(generated_rows=result["rows"], shortfall=result["shortfall"], stop_reason=result["stop_reason"])
        snapshot = progress.stop(result["status"])
        result.update({"usage": snapshot["usage"], "elapsed_seconds": snapshot["elapsed_seconds"], "request": request,
                       "campaign_id": rounds[-1]["campaign_id"] if rounds else None, "rounds": rounds,
                       "page_budget": max_pages, "page_budget_allocated": allocated,
                       "source_dataset": config.l2_dataset, "qa_dataset": config.l3_dataset,
                       "run": str(run), "warehouse": str(warehouse)})
        from .qa_artifacts import export_stages
        result["artifacts"] = export_stages(warehouse, run / "artifacts")
        (run / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    except (Exception, KeyboardInterrupt) as exc:
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        snapshot = progress.stop(status)
        (run / "report.json").write_text(json.dumps({"status": status, "error": str(exc), "usage": snapshot["usage"]}, ensure_ascii=False, indent=2) + "\n")
        raise


def _execute_sources(runner, request, warehouse, run, config, meter, stop_after):
    from .qa_artifacts import export_stages
    progress = QAProgress(warehouse, run, config, meter)
    progress.start()
    try:
        campaign = runner.start(request, config)
        (run / 'campaign.json').write_text(json.dumps(campaign, ensure_ascii=False, indent=2))
        pipeline = campaign.get('pipeline') or {}
        empty = str(pipeline.get('error', '')).startswith('no records materialized') and not any(
            stage.get('failed') for stage in pipeline.get('stages', [])) and not campaign.get('queue', {}).get('failed')
        if campaign.get('status') != 'completed' and not empty:
            raise RuntimeError('Source collection did not complete; inspect campaign.json')
        snapshot = progress.stop('completed')
        result = {'status': 'completed', 'stop_after': stop_after, 'rows': 0,
                  'request': request, 'factual_verification': 'not_performed',
                  'pages_collected': snapshot['pages_collected'], 'sources_accepted': snapshot['sources_accepted'],
                  'source_dataset': config.l2_dataset, 'qa_dataset': config.l3_dataset,
                  'usage': snapshot['usage'], 'elapsed_seconds': snapshot['elapsed_seconds'],
                  'artifacts': export_stages(warehouse, run / 'artifacts')}
        (run / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    except (Exception, KeyboardInterrupt) as exc:
        snapshot = progress.stop('failed')
        (run / 'report.json').write_text(json.dumps({'status': 'failed', 'error': str(exc), 'usage': snapshot['usage']}))
        raise
