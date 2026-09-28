"""Turn a badcase report into the standalone acquisition-worker command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        value = json.loads(text)
    else:
        import yaml

        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("badcase file must contain a mapping")
    return value


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _request(data: dict[str, Any]) -> tuple[str, list[str], list[str], int]:
    domain = str(data.get("badcase_domain") or data.get("domain") or data.get("category") or "").strip()
    failure_types = _as_strings(
        data.get("failure_types") or data.get("failure_taxonomy") or data.get("error_types")
    )
    keywords = _as_strings(data.get("keywords") or data.get("search_keywords"))
    focus = _as_strings(data.get("focus_keywords") or data.get("capability_buckets"))
    if domain:
        keywords.insert(0, domain)
        focus.insert(0, domain)
    keywords.extend(failure_types)
    focus.extend(failure_types)
    objective = str(data.get("objective") or data.get("request") or "").strip()
    if not objective:
        objective = "Collect datasets and authoritative web pages for badcases"
        if domain:
            objective += f" in the {domain} domain"
        if failure_types:
            objective += ": " + ", ".join(failure_types)
    target = int(data.get("target_datasets") or data.get("target_dataset_count") or 1)
    return objective, list(dict.fromkeys(keywords)), list(dict.fromkeys(focus)), max(1, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run acquisition from a badcase JSON/YAML report")
    parser.add_argument("--badcase", type=Path, required=True)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data = _load(args.badcase)
    objective, keywords, focus, target = _request(data)
    if not args.dry_run:
        init_command = [
            sys.executable,
            "-m",
            "dataflowwebagent.skills.ObtainerCLI.cli",
            "dm",
            "--root",
            str(args.warehouse),
            "init",
            "--json",
        ]
        initialized = subprocess.run(
            init_command,
            check=False,
            capture_output=True,
            text=True,
        )
        if initialized.returncode != 0:
            if initialized.stdout:
                print(initialized.stdout, end="")
            if initialized.stderr:
                print(initialized.stderr, end="", file=sys.stderr)
            return initialized.returncode
    command = [
        sys.executable,
        "-m",
        "dataflowwebagent.skills.ObtainerCLI.cli",
        "dm",
        "--root",
        str(args.warehouse),
        "dataset-acquisition-agent",
        "start",
        "--run",
        str(args.run),
        "--objective",
        objective,
        "--analysis-report",
        str(args.badcase.resolve()),
        "--keywords",
        ",".join(keywords),
        "--target-datasets",
        str(target),
    ]
    for item in focus:
        command.extend(("--focus-keywords", item))
    if args.dry_run:
        print(json.dumps({"command": command, "objective": objective, "keywords": keywords, "focus_keywords": focus}, ensure_ascii=False, indent=2))
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
