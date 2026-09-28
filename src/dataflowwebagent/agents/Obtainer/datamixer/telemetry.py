"""Run-scoped model accounting shared by campaign and operator threads."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time

_ACTIVE: dict[str, "UsageMeter"] = {}
_LOCK = threading.Lock()


def meter_for(key: str) -> "UsageMeter | None":
    with _LOCK:
        return _ACTIVE.get(key)


class UsageMeter:
    def __init__(self, warehouse: Path, run: Path):
        self.key = str(warehouse.resolve())
        self.path = run / "api_calls.jsonl"
        self.lock = threading.Lock()
        self.calls = self.failed = self.finished = self.with_usage = 0
        self.input_tokens = self.output_tokens = 0

    def __enter__(self):
        with _LOCK:
            if self.key in _ACTIVE:
                raise RuntimeError("Another monitored run is using this warehouse.")
            _ACTIVE[self.key] = self
        return self

    def __exit__(self, *args):
        with _LOCK:
            _ACTIVE.pop(self.key, None)

    def start(self) -> int:
        with self.lock:
            self.calls += 1
            return self.calls

    def finish(self, call_id: int, model: str, response: dict | None, error: bool, elapsed: float):
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
        outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
        known = all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (incoming, outgoing))
        # Never store prompts, responses, keys, endpoints, or raw provider errors.
        record = {"call": call_id, "model": model, "status": "failed" if error else "completed",
                  "elapsed_seconds": round(elapsed, 3), "timestamp": time.time(),
                  "input_tokens": incoming if known else None,
                  "output_tokens": outgoing if known else None}
        with self.lock:
            self.finished += 1
            self.failed += int(error)
            self.with_usage += int(known)
            if known:
                self.input_tokens += incoming
                self.output_tokens += outgoing
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        with self.lock:
            return {"calls": self.calls, "failed_calls": self.failed,
                    "in_flight": self.calls - self.finished,
                    "calls_with_usage": self.with_usage,
                    "calls_without_usage": self.finished - self.with_usage,
                    "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                    "usage_complete": self.with_usage == self.calls,
                    "scope": "model_api_only", "cost": None}
