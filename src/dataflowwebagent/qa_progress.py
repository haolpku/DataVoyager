"""Read-only progress snapshots for the prompt-to-QA workflow."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

from .agents.Obtainer.datamixer.telemetry import UsageMeter

_LABELS = {"webpage_to_pt": "extracting text", "domain_classify": "checking relevance",
           "topic_quality_filter": "filtering sources", "pt_to_sft_qa": "generating QA",
           "sft_validate": "validating QA"}


def format_progress(snapshot: dict) -> str:
    usage = snapshot["usage"]
    coverage = "" if usage["usage_complete"] else " (partial; some usage unavailable or pending)"
    quantity = (f"unique QA {snapshot.get('generated_rows', 0)}/{snapshot['target_rows']} | "
                if snapshot.get("target_rows") else "")
    return (f"[{snapshot['elapsed_seconds']:.0f}s] {snapshot['stage']} | "
            f"pages {snapshot['pages_collected']} | accepted sources {snapshot['sources_accepted']} | "
            f"QA candidates {snapshot['qa_candidates']} | {quantity}API calls {usage['calls']} "
            f"({usage['failed_calls']} failed, {usage['in_flight']} active) | "
            f"tokens {usage['input_tokens']} in / {usage['output_tokens']} out{coverage}")


class QAProgress:
    def __init__(self, warehouse: Path, run: Path, config, meter: UsageMeter, pipeline_reader=None):
        self.warehouse, self.run, self.config, self.meter = warehouse, run, config, meter
        self.pipeline_reader = pipeline_reader
        self.started = time.monotonic()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="qa-progress", daemon=True)
        self.status = "running"
        self.stage = "searching / collecting sources"
        self.quantity = {}

    def start(self):
        self.write()
        self.thread.start()

    def stop(self, status: str):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join()
        self.status = self.stage = status
        return self.write()

    def _loop(self):
        while not self.stop_event.wait(2):
            self.write()

    def write(self) -> dict:
        counts = {self.config.dataset: 0, self.config.l2_dataset: 0, self.config.l3_dataset: 0}
        pipeline = {}
        warning = None
        try:
            with closing(sqlite3.connect((self.warehouse / "catalog.db").as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                for name, count in conn.execute(
                    "SELECT d.name, COUNT(*) FROM samples s JOIN datasets d ON d.id=s.dataset_id "
                    "WHERE d.name IN (?, ?, ?) GROUP BY d.name", tuple(counts)):
                    counts[name] = count
            if self.pipeline_reader:
                pipeline = self.pipeline_reader()
            else:
                with closing(sqlite3.connect((self.warehouse / "webagent_queue.sqlite").as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                    row = conn.execute("SELECT pipeline_json FROM campaigns WHERE dataset=? ORDER BY created_at DESC LIMIT 1",
                                       (self.config.dataset,)).fetchone()
                    if row and row[0]:
                        pipeline = json.loads(row[0])
        except (sqlite3.Error, ValueError) as exc:
            warning = str(exc)
        if self.status == "running":
            active = pipeline.get("active_stages") or []
            self.stage = ", ".join(_LABELS.get(name, name) for name in active) or "searching / collecting sources"
            if pipeline.get("status") == "completed":
                self.stage = "exporting QA"
        snapshot = {"status": self.status, "stage": self.stage, "pid": os.getpid(),
                    "updated_at": time.time(), "elapsed_seconds": round(time.monotonic() - self.started, 1),
                    "pages_collected": counts[self.config.dataset],
                    "sources_accepted": counts[self.config.l2_dataset],
                    "qa_candidates": counts[self.config.l3_dataset],
                    "stages": pipeline.get("stages", []), "usage": self.meter.snapshot(), **dict(self.quantity)}
        if warning:
            snapshot["progress_warning"] = warning
        temporary = self.run / "progress.json.tmp"
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.run / "progress.json")
        print(format_progress(snapshot), file=sys.stderr, flush=True)
        return snapshot
