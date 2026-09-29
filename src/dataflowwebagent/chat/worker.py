"""One isolated dataset process per immutable chat version."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

from ..agents.Obtainer.datamixer.cas import ContentStore
from ..agents.Obtainer.datamixer.store import DataStore
from ..agents.Obtainer.datamixer.telemetry import UsageMeter
from ..qa_pipeline import export_qa, qa_records, pipeline_spec, resolve_model, run_qa
from ..qa_progress import QAProgress
from ..qa_quantity import resolve_target, quantity_result


def read_sources(warehouse: Path, level="L2"):
    """Read original text without opening a mutable DataStore on the old version."""
    with closing(sqlite3.connect(warehouse.resolve().joinpath("catalog.db").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        cas = ContentStore(warehouse)
        for row in conn.execute("SELECT cid,tags_json,domain FROM samples WHERE quality_level=? ORDER BY created_at,sample_id", (level,)):
            yield {"content": cas.get_json(row["cid"]), "tags": json.loads(row["tags_json"] or "{}"), "domain": row["domain"]}


def revise(request: str, *, base_warehouse: Path, warehouse: Path, run: Path, output: Path,
           target_rows: int | None = None) -> dict:
    from ..agents.Obtainer.datamixer.operators.pipeline import run_pipeline
    target_rows = resolve_target(request, target_rows)
    run.mkdir(parents=True, exist_ok=True)
    with (run / "request.json").open("x", encoding="utf-8") as handle:
        json.dump({"objective": request, "mode": "revise", "base_warehouse": str(base_warehouse)}, handle, ensure_ascii=False)
    store = DataStore.init(warehouse)
    state, lock = {}, threading.Lock()
    def read_progress():
        with lock:
            return dict(state)
    def update_progress(value):
        with lock:
            state.update(value)
            state["active_stages"] = [value["current_stage"]] if value.get("current_stage") else []
    try:
        model = resolve_model(warehouse)
        source = store.catalog.add_dataset(name="reused_sources", source="revision")
        copied = store.ingest_records(source, read_sources(base_warehouse), defaults={"quality_level": "L2"})
        if not copied.written:
            raise ValueError("No accepted source text is available to revise; start a new collection")
        spec = pipeline_spec(request, model)["pipeline"]
        spec["source"] = {"dataset": "reused_sources"}
        # Re-evaluate the full saved corpus against revised requirements.
        # It contains text, so evidence_prepare also works without raw HTML.
        config = SimpleNamespace(dataset="no_new_crawl", l2_dataset="reused_sources", l3_dataset="qa_l3")
        with UsageMeter(warehouse, run) as meter:
            progress = QAProgress(warehouse, run, config, meter, pipeline_reader=read_progress)
            try:
                progress.start()
                pipeline = run_pipeline(store, spec, batch_size=2, progress_callback=update_progress)
                (run / "pipeline-report.json").write_text(json.dumps(pipeline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                rows, _, _ = qa_records(store, "qa_l3")
                if rows or not target_rows:
                    result = export_qa(store, "qa_l3", output, target_rows=target_rows)
                else:
                    result = {"rows": 0, "output": None, "sources": None, "factual_verification": "model_source_review"}
                result.update(quantity_result(result["rows"], target_rows, "existing_sources_exhausted"))
                progress.quantity = {"target_rows": target_rows, "generated_rows": result["rows"], "shortfall": result["shortfall"]}
                snapshot = progress.stop(result["status"])
                result.update(request=request, mode="revise", source_dataset="reused_sources",
                              qa_dataset="qa_l3", warehouse=str(warehouse), run=str(run),
                              reused_sources=copied.written, usage=snapshot["usage"], elapsed_seconds=snapshot["elapsed_seconds"])
                from ..qa_artifacts import export_stages
                result["artifacts"] = export_stages(warehouse, run / "artifacts")
                (run / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                return result
            except (Exception, KeyboardInterrupt) as exc:
                snapshot = progress.stop("failed")
                (run / "report.json").write_text(json.dumps({"status": "failed", "error": str(exc), "usage": snapshot["usage"]}), encoding="utf-8")
                raise
    finally:
        store.close()


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(sys.argv[1]).resolve().parent
    kwargs = {"warehouse": root / "warehouse", "run": root / "run", "output": root / "qa.jsonl",
              "target_rows": job.get("target_rows")}
    if job["action"] == "revise":
        revise(job["request"], base_warehouse=Path(job["base_warehouse"]), **kwargs)
    else:
        run_qa(job["request"], max_pages=job["max_pages"], stop_after=job.get("stop_after", "qa"),
               base_warehouse=Path(job["base_warehouse"]) if job.get("base_warehouse") else None, **kwargs)


if __name__ == "__main__":
    main()
