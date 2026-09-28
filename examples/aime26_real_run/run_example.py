#!/usr/bin/env python3
"""Verify the recorded AIME run, or execute its model-backed pipeline again."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parents[2] / "src"


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def verify() -> int:
    status = json.loads((HERE / "run_status.json").read_text(encoding="utf-8"))
    input_rows = _rows(HERE / "trial_input.jsonl")
    output_rows = _rows(HERE / "trial_output.jsonl")
    benchmark_rows = _rows(HERE / "benchmark_samples.jsonl")
    expected = {
        "input_rows": len(input_rows),
        "output_rows": len(output_rows),
        "benchmark_samples_reviewed": len(benchmark_rows),
    }
    for key, actual in expected.items():
        recorded = status[key]
        if actual != recorded:
            raise SystemExit(f"{key}: recorded={recorded}, files={actual}")
    if not output_rows:
        raise SystemExit("trial_output.jsonl is empty")
    required = {"sample_id", "instruction", "output", "quality_score"}
    missing = sorted(required - set(output_rows[0]))
    if missing:
        raise SystemExit(f"trial output missing fields: {missing}")
    print(json.dumps({**status, "verified_files": expected}, ensure_ascii=False, indent=2))
    return 0


def execute(work_dir: Path | None) -> int:
    target = work_dir or Path(tempfile.mkdtemp(prefix="dataflowwebagent-aime26-"))
    target.mkdir(parents=True, exist_ok=True)
    cache = target / "cache"
    output = target / "trial_output.jsonl"
    funnel = target / "trial_funnel.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(SOURCE_ROOT), env.get("PYTHONPATH", "")) if item
    )
    env.update(
        {
            "DATAFLOW_INPUT": str(HERE / "trial_input.jsonl"),
            "DATAFLOW_CACHE_DIR": str(cache),
            "DATAFLOW_OUTPUT": str(output),
            "DATAFLOW_FUNNEL": str(funnel),
            "DATAFLOW_PREFIX": "aime26_amo_sft_example",
            "AIME26_BENCHMARK": str(HERE / "benchmark_samples.jsonl"),
            "AMO_BENCHMARK": str(HERE / "benchmark_samples.jsonl"),
        }
    )
    completed = subprocess.run(
        [sys.executable, str(HERE / "pipeline.py")],
        cwd=HERE,
        env=env,
        check=False,
    )
    print(json.dumps({"work_dir": str(target), "output": str(output)}, indent=2))
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run the copied DataFlow pipeline; requires dataflow and DF_API_URL/DF_API_KEY",
    )
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    return execute(args.work_dir) if args.execute else verify()


if __name__ == "__main__":
    raise SystemExit(main())
