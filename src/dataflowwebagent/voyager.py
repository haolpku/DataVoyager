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
        description="DataVoyager: turn a prompt into a QA training dataset",
        epilog="Advanced commands: datavoyager dm --help; datavoyager badcase --help",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="open a local multi-turn dataset chat workspace")
    chat.add_argument("--root", type=Path, default=Path("runs/chat"))
    chat.add_argument("--port", type=int, default=8765)
    status = sub.add_parser("status", help="show a QA run's latest progress and API usage")
    status.add_argument("--run", type=Path, required=True)
    status.add_argument("--json", action="store_true")
    build = sub.add_parser("build", help="start acquisition from a natural-language request")
    build.add_argument("request", help="describe the desired dataset and quality requirements")
    build.add_argument("--domain", default="")
    build.add_argument("--keywords", default="")
    build.add_argument("--focus", action="append", default=[])
    build.add_argument("--target-datasets", type=_positive, default=1)
    build.add_argument("--output", type=Path, help="build and export a web-sourced QA dataset as Alpaca JSONL")
    build.add_argument("--max-pages", type=_positive, default=20, help="page budget for --output mode (default: 20)")
    build.add_argument("--target-rows", type=_positive, help="desired unique QA count; inferred from explicit request counts when omitted")
    build.add_argument("--max-rounds", type=_positive, default=5, help="bounded refill rounds within --max-pages (default: 5)")
    build.add_argument("--warehouse", type=Path)
    build.add_argument("--run", type=Path)
    build.add_argument("--dry-run", action="store_true", help="print the request without network calls or writes")
    args = parser.parse_args(argv)
    if args.command == "chat":
        from .chat.server import serve
        try:
            serve(args.root, args.port)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.command == "status":
        from .qa_progress import format_progress
        try:
            snapshot = json.loads((args.run.expanduser() / "progress.json").read_text())
        except (OSError, ValueError) as exc:
            print(f"Cannot read run progress: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(snapshot, ensure_ascii=False, indent=2) if args.json else format_progress(snapshot))
        return 0
    if not args.request.strip():
        parser.error("request must not be empty")
    spec = {
        "objective": args.request.strip(),
        "domain": args.domain,
        "keywords": args.keywords,
        "focus_keywords": args.focus,
        "target_datasets": args.target_datasets,
    }
    output = args.output.expanduser().resolve() if args.output else None
    default_run = output.with_name(output.name + ".run") if output else Path("runs/acquisition")
    run = (args.run or default_run).expanduser().resolve()
    warehouse = (args.warehouse or (run / "warehouse" if output else Path("runs/warehouse"))).expanduser().resolve()
    request_path = run / "request.json"
    if args.dry_run:
        preview = {"status": "dry_run", "request": spec,
                   "warehouse": str(warehouse), "run": str(run)}
        if output:
            from .qa_quantity import resolve_target
            preview.update({"mode": "web_qa", "output": str(output), "format": "alpaca", "max_pages": args.max_pages,
                            "target_rows": resolve_target(args.request, args.target_rows), "max_rounds": args.max_rounds})
        print(json.dumps(preview,
                         ensure_ascii=False, indent=2))
        return 0
    if output:
        from .qa_pipeline import run_qa
        try:
            result = run_qa(args.request.strip(), warehouse=warehouse, run=run, output=output,
                            max_pages=args.max_pages, focus=[x for x in [args.domain, *args.focus] if x],
                            target_rows=args.target_rows, max_rounds=args.max_rounds)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["status"] == "needs_confirmation" else 0
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
