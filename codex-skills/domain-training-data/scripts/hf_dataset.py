#!/usr/bin/env python3
"""Inspect and read public Hugging Face Dataset Server datasets without datasets."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

HF_API = "https://huggingface.co/api/datasets/"
ROWS_API = "https://datasets-server.huggingface.co/rows"
SPLITS_API = "https://datasets-server.huggingface.co/splits"
HEADERS = {"Accept": "application/json", "User-Agent": "domain-training-data/1.0"}


class HFError(RuntimeError):
    pass


def get_json(url: str, *, retries: int = 3) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers=HEADERS)
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise HFError("response is not a JSON object")
            return payload
        except (HTTPError, URLError, TimeoutError, ValueError, HFError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(attempt + 1)
    detail = f"HTTP {last.code}: {last.reason}" if isinstance(last, HTTPError) else str(last)
    raise HFError(detail) from last


def dataset_metadata(dataset_id: str) -> dict[str, Any]:
    return get_json(HF_API + quote(dataset_id, safe="/"))


def splits(dataset_id: str) -> list[dict[str, Any]]:
    payload = get_json(SPLITS_API + "?" + urlencode({"dataset": dataset_id}))
    values = payload.get("splits")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def inspect(dataset_id: str) -> dict[str, Any]:
    metadata = dataset_metadata(dataset_id)
    card = metadata.get("cardData") if isinstance(metadata.get("cardData"), dict) else {}
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    return {
        "dataset_id": dataset_id,
        "description": metadata.get("description", ""),
        "tags": tags,
        "license": card.get("license") or next((tag[9:] for tag in tags if isinstance(tag, str) and tag.startswith("license:")), None),
        "languages": card.get("language") or [tag[5:] for tag in tags if isinstance(tag, str) and tag.startswith("lang:")],
        "task_categories": card.get("task_categories") or [tag[5:] for tag in tags if isinstance(tag, str) and tag.startswith("task_categories:")],
        "configs_and_splits": splits(dataset_id),
    }


def read_rows(dataset_id: str, config: str, split: str, limit: int) -> list[dict[str, Any]]:
    if limit < 1 or limit > 1000:
        raise HFError("limit must be 1–1000")
    query = urlencode({"dataset": dataset_id, "config": config, "split": split, "offset": 0, "length": limit})
    payload = get_json(ROWS_API + "?" + query)
    values = payload.get("rows")
    if not isinstance(values, list):
        raise HFError("Dataset Server response did not contain rows")
    return [dict(value["row"]) for value in values if isinstance(value, dict) and isinstance(value.get("row"), dict)]


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "rows", "download"):
        command = commands.add_parser(name)
        command.add_argument("dataset_id")
        if name != "inspect":
            command.add_argument("--config", required=True)
            command.add_argument("--split", required=True)
            command.add_argument("--limit", type=int, default=3)
        if name == "download":
            command.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            emit(inspect(args.dataset_id))
            return 0
        rows = read_rows(args.dataset_id, args.config, args.split, args.limit)
        if args.command == "rows":
            emit({"dataset_id": args.dataset_id, "config": args.config, "split": args.split, "rows": rows})
            return 0
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for index, row in enumerate(rows):
                row.update({"_source_dataset": args.dataset_id, "_source_config": args.config, "_source_split": args.split, "_source_row": index})
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        report = output.with_suffix(output.suffix + ".report.json")
        report.write_text(json.dumps({"dataset_id": args.dataset_id, "config": args.config, "split": args.split, "rows_read": len(rows), "rows_written": len(rows), "output": str(output.resolve())}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit({"output": str(output.resolve()), "report": str(report.resolve()), "rows_written": len(rows)})
        return 0
    except HFError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
