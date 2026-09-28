"""Natural-language front door to the existing acquisition runtime."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, badcase_pipeline


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Preserve the full existing CLI without duplicating its argument parser.
    if argv and argv[0] == "dm":
        from .skills.ObtainerCLI.cli import run as legacy_run
        return legacy_run(argv)
    if argv and argv[0] == "badcase":
        return badcase_pipeline.main(argv[1:])
    parser = argparse.ArgumentParser(
        description="DataVoyager: natural-language requirements to domain datasets",
        epilog="Advanced commands: datavoyager dm --help; datavoyager badcase --help",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="start acquisition from a natural-language request")
    build.add_argument("request", help="describe the desired dataset and quality requirements")
    build.add_argument("--domain", default="")
    build.add_argument("--keywords", default="")
    build.add_argument("--focus", action="append", default=[])
    build.add_argument("--target-datasets", type=_positive, default=1)
    build.add_argument("--warehouse", type=Path, default=Path("runs/warehouse"))
    build.add_argument("--run", type=Path, default=Path("runs/acquisition"))
    build.add_argument("--dry-run", action="store_true", help="print the request without network calls or writes")
    args = parser.parse_args(argv)
    if not args.request.strip():
        parser.error("request must not be empty")
    spec = {
        "objective": args.request.strip(),
        "domain": args.domain,
        "keywords": args.keywords,
        "focus_keywords": args.focus,
        "target_datasets": args.target_datasets,
    }
    warehouse = args.warehouse.expanduser().resolve()
    run = args.run.expanduser().resolve()
    request_path = run / "request.json"
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "request": spec,
                          "warehouse": str(warehouse), "run": str(run)},
                         ensure_ascii=False, indent=2))
        return 0
    run.mkdir(parents=True, exist_ok=True)
    # Never overwrite the request belonging to an existing acquisition run.
    try:
        with request_path.open("x", encoding="utf-8") as handle:
            json.dump(spec, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError:
        parser.error(f"request already exists at {request_path}; use a new --run directory")
    return badcase_pipeline.main([
        "--badcase", str(request_path), "--warehouse", str(warehouse), "--run", str(run),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
