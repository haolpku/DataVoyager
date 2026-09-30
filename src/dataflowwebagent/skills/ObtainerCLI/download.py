from __future__ import annotations

import json
import os
import re
import csv
import io
import zipfile
import time
from itertools import islice
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import urlopen

from dataflowwebagent.utils.hf_endpoints import (
    DEFAULT_HF_ENDPOINTS,
    apply_hf_endpoint as _apply_hf_endpoint,
    endpoint_reachable as _endpoint_reachable,
    hf_endpoint_chain as _hf_endpoint_chain,
)

from .errors import ObtainerCliError
from .models import canonical_json, utc_now


MAX_ROWS_PER_DATASET = 100_000
MAX_BYTES_PER_DATASET = 2 * 1024 * 1024 * 1024
# A source-row sample never needs a multi-gigabyte Hub artifact.  Avoid Xet
# reconstruction for huge monolithic files; it can otherwise stall a whole
# selected-source batch before the next source is tried.
MAX_DIRECT_HUB_FILE_BYTES = 32 * 1024 * 1024
_datasets_runtime_usable: bool | None = None


def _ensure_hf_mirror_env(*, cache_root: Path | None = None) -> None:
    endpoint = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HF_HUB_ENDPOINT")
        or DEFAULT_HF_ENDPOINTS[0]
    )
    os.environ.setdefault("HF_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if cache_root is not None:
        # The process may be launched by Task Scheduler, whose profile cache
        # is not necessarily writable.  Keep Hub cache writes in this run's
        # workspace instead of silently failing every direct-file fallback.
        hub_cache = cache_root / "hub"
        hub_cache.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_root)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub_cache)


def _load_hf_dataset(dataset_id: str, *, split: str, streaming: bool) -> Any:
    """Load a dataset with multi-level endpoint fallback.

    Each endpoint is probed first (short timeout, no retry), then tried in
    order (explicit config, official hub, mirrors, configured extras).  The
    per-endpoint config auto-discovery fallback is kept: when a bare load
    fails, the first config is resolved and retried against the same endpoint
    before moving on.  Only when every endpoint fails is a combined error
    raised, so one dead mirror cannot sink the whole download run.
    """
    global _datasets_runtime_usable
    if _datasets_runtime_usable is False:
        raise ObtainerCliError(
            "HF_DATASETS_RUNTIME_UNAVAILABLE",
            "The optional datasets runtime failed to import earlier in this process",
        )
    errors: list[str] = []
    for endpoint in _hf_endpoint_chain():
        if not _endpoint_reachable(endpoint):
            errors.append(f"{endpoint}: unreachable (probe failed)")
            continue
        _apply_hf_endpoint(endpoint)
        try:
            result = _load_hf_dataset_once(dataset_id, split=split, streaming=streaming)
            _datasets_runtime_usable = True
            return result
        except (ImportError, AttributeError) as exc:
            # This environment has a broken NumPy/datasets binary import.
            # Retrying it against every mirror only emits noise and delays the
            # bounded Hub/Dataset-Server fallbacks below.
            _datasets_runtime_usable = False
            raise ObtainerCliError(
                "HF_DATASETS_RUNTIME_UNAVAILABLE",
                f"datasets could not import: {type(exc).__name__}: {exc}",
            ) from exc
        except Exception as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {str(exc)[:300]}")
            continue
    raise ObtainerCliError(
        "HF_DOWNLOAD_ALL_ENDPOINTS_FAILED",
        f"all HuggingFace endpoints failed for dataset {dataset_id!r}",
        hint="\n".join(errors),
    )


def _load_hf_dataset_once(dataset_id: str, *, split: str, streaming: bool) -> Any:
    from datasets import get_dataset_config_names, load_dataset
    from datasets.download.download_config import DownloadConfig

    kwargs: dict[str, Any] = {
        "split": split,
        "streaming": streaming,
        # Fail fast on a bad endpoint so the next mirror in the chain is tried
        # promptly instead of burning huggingface_hub's built-in retries.
        "download_config": DownloadConfig(max_retries=0),
    }
    try:
        return load_dataset(dataset_id, **kwargs)
    except Exception as first_error:
        try:
            configs = get_dataset_config_names(dataset_id)
        except Exception:
            configs = []
        if not configs:
            raise first_error
        return load_dataset(dataset_id, configs[0], **kwargs)


def _load_hf_dataset_server_rows(dataset_id: str, *, split: str, limit: int) -> list[dict[str, Any]]:
    """Read a small public sample without importing the optional datasets stack.

    The Dataset Server exposes normalized rows for viewer-enabled Hub datasets.
    This is a bounded fallback for environments where ``datasets`` cannot load
    its binary dependencies; it is not used for bulk download.
    """
    query = urlencode({"dataset": dataset_id, "config": "default", "split": split,
                       "offset": 0, "length": min(max(1, limit), 100)})
    url = "https://datasets-server.huggingface.co/rows?" + query
    error = None
    for attempt in range(2):
        try:
            with urlopen(url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            error = exc
            if attempt < 1:
                time.sleep(attempt + 1)
    else:
        raise ObtainerCliError("HF_DATASET_SERVER_ROWS_FAILED",
                               f"Dataset Server could not read {dataset_id!r}: {error}") from error
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ObtainerCliError("HF_DATASET_SERVER_ROWS_FAILED",
                               f"Dataset Server returned no rows for {dataset_id!r}")
    return [dict(item.get("row") or {}) for item in rows if isinstance(item, dict) and isinstance(item.get("row"), dict)]


def _load_hf_hub_file_rows(dataset_id: str, *, split: str, limit: int) -> list[dict[str, Any]]:
    """Read a bounded structured file directly from the Hub as a datasets-free fallback."""
    try:
        from huggingface_hub import HfApi, hf_hub_download
        api = HfApi()
        files = api.list_repo_files(dataset_id, repo_type="dataset")
    except Exception as exc:
        raise ObtainerCliError("HF_HUB_FILE_LIST_FAILED", f"Could not list files for {dataset_id!r}: {exc}") from exc
    extensions = (".jsonl", ".ndjson", ".json", ".csv", ".tsv")
    named = [name for name in files if Path(name).suffix.lower() in extensions]
    preferred = next((name for name in named if Path(name).stem.casefold() == split.casefold()), None)
    filename = preferred or next((name for name in named if split.casefold() in Path(name).stem.casefold()), None)
    filename = filename or (named[0] if named else None)
    if not filename:
        raise ObtainerCliError("HF_HUB_STRUCTURED_FILE_MISSING", f"No JSON/CSV source file for {dataset_id!r}")
    try:
        info = api.get_paths_info(dataset_id, filename, repo_type="dataset", expand=True)
        size = next((getattr(entry, "size", None) for entry in info if getattr(entry, "path", None) == filename), None)
        if isinstance(size, int) and size > MAX_DIRECT_HUB_FILE_BYTES:
            raise ObtainerCliError(
                "HF_HUB_FILE_TOO_LARGE",
                f"Refusing {filename!r} ({size} bytes): bounded source sampling only reads files up to {MAX_DIRECT_HUB_FILE_BYTES} bytes",
            )
    except ObtainerCliError:
        raise
    except Exception:
        # Metadata is advisory.  If its endpoint is unavailable, retain the
        # direct-file fallback for normally sized datasets.
        pass
    try:
        path = Path(hf_hub_download(repo_id=dataset_id, repo_type="dataset", filename=filename))
    except Exception as exc:
        raise ObtainerCliError("HF_HUB_FILE_DOWNLOAD_FAILED", f"Could not download {filename!r}: {exc}") from exc
    suffix = path.suffix.casefold()
    rows: list[dict[str, Any]] = []
    if suffix in {".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in islice(handle, limit):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(item if isinstance(item, dict) else {"value": item})
    elif suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        values = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else payload
        rows = [item if isinstance(item, dict) else {"value": item} for item in (values if isinstance(values, list) else [values])][:limit]
    else:
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            rows = [dict(row) for row in islice(csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ","), limit)]
    if not rows:
        raise ObtainerCliError("HF_HUB_FILE_EMPTY", f"No rows found in {filename!r} for {dataset_id!r}")
    return rows


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "__", value.strip())
    return name.strip("._-") or "dataset"


def _read_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise ObtainerCliError(
            "MANIFEST_NOT_FOUND",
            f"download manifest not found: {manifest_path}",
            hint="Run searchagent first or pass an existing searchagent_manifest.json.",
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ObtainerCliError(
            "INVALID_MANIFEST",
            "download manifest must be a JSON object",
            hint="Expected a SearchAgent manifest with a download_list field.",
        )
    return payload


def _iter_download_items(manifest: dict[str, Any], *, limit: int = 0) -> list[dict[str, Any]]:
    raw_items = manifest.get("download_list") or manifest.get("candidates") or []
    if not isinstance(raw_items, list):
        raise ObtainerCliError(
            "INVALID_MANIFEST",
            "manifest download_list must be a list",
            hint="Re-run searchagent to generate a valid manifest.",
        )
    items = [item for item in raw_items if isinstance(item, dict)]
    if limit > 0:
        return items[:limit]
    return items


def _row_text(row: dict[str, Any]) -> str:
    for key in ("text", "content", "question", "problem", "query", "instruction"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return canonical_json(row)


def _normalize_row(
    row: dict[str, Any],
    *,
    dataset_id: str,
    split: str,
    row_index: int,
    source_domain: str = "huggingface",
    source_uri_prefix: str = "",
) -> dict[str, Any]:
    text = _row_text(row)
    question = row.get("question") or row.get("problem") or row.get("query") or row.get("instruction") or ""
    answer = row.get("answer") or row.get("target") or row.get("output") or row.get("response") or ""
    normalized = dict(row)
    normalized.setdefault("text", text)
    normalized.setdefault("instruction", question if isinstance(question, str) else "")
    normalized.setdefault("input", question if isinstance(question, str) else "")
    normalized.setdefault("output", answer if isinstance(answer, str) else canonical_json(answer))
    normalized.setdefault("source_domain", source_domain)
    normalized.setdefault(
        "source_uri",
        source_uri_prefix or f"hf://datasets/{dataset_id}/{split}#{row_index}",
    )
    normalized.setdefault("split", split)
    return normalized


def _write_jsonl(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    bytes_written = 0
    truncated = False
    with path.open("wb") as handle:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            if max_bytes > 0 and bytes_written + len(line) > max_bytes:
                truncated = True
                break
            handle.write(line)
            count += 1
            bytes_written += len(line)
    return {
        "rows_written": count,
        "bytes_written": bytes_written,
        "byte_cap_reached": truncated,
    }


def _export_huggingface_jsonl(
    *,
    item: dict[str, Any],
    output_root: Path,
    split: str,
    max_rows: int,
    max_bytes_per_dataset: int,
    streaming: bool,
) -> dict[str, Any]:
    dataset_id = str(
        item.get("dataset_id")
        or (item.get("download") or {}).get("dataset_id")
        or ""
    ).strip()
    if not dataset_id:
        return {
            "ok": False,
            "status": "failed",
            "source": "huggingface",
            "error": "missing dataset_id",
            "candidate": item,
        }

    _ensure_hf_mirror_env(cache_root=output_root / ".hf_cache")
    effective_max_rows = _effective_max_rows(max_rows)
    effective_max_bytes = _effective_max_bytes(max_bytes_per_dataset)
    endpoint_used = os.environ.get("HF_ENDPOINT") or ""
    try:
        dataset = _load_hf_dataset(dataset_id, split=split, streaming=streaming)
        selected_rows = islice(dataset, effective_max_rows)
    except ObtainerCliError:
        # ``datasets`` may be unavailable or binary-incompatible even though
        # the Hub and its row service are reachable.  Preserve a small usable
        # source sample instead of rejecting every candidate.
        try:
            selected_rows = iter(_load_hf_hub_file_rows(dataset_id, split=split, limit=effective_max_rows))
            endpoint_used = "huggingface_hub:file"
        except ObtainerCliError:
            selected_rows = iter(_load_hf_dataset_server_rows(dataset_id, split=split, limit=effective_max_rows))
            endpoint_used = "https://datasets-server.huggingface.co/rows"
    rows_iter = (
        _normalize_row(dict(row), dataset_id=dataset_id, split=split, row_index=index)
        for index, row in enumerate(selected_rows, 1)
    )
    dataset_name = _safe_name(dataset_id)
    records_path = output_root / "records" / f"{dataset_name}.{split}.jsonl"
    write_result = _write_jsonl(records_path, rows_iter, max_bytes=effective_max_bytes)
    rows_written = int(write_result["rows_written"])
    return {
        "ok": rows_written > 0,
        "status": "completed" if rows_written > 0 else "empty",
        "source": "huggingface",
        "dataset_id": dataset_id,
        "split": split,
        "endpoint_used": endpoint_used,
        "rows_written": rows_written,
        "bytes_written": int(write_result["bytes_written"]),
        "max_rows_requested": max_rows,
        "max_rows_effective": effective_max_rows,
        "row_cap_applied": max_rows == 0 or max_rows > effective_max_rows,
        "max_bytes_requested": max_bytes_per_dataset,
        "max_bytes_effective": effective_max_bytes,
        "byte_cap_applied": bool(write_result["byte_cap_reached"]),
        "truncated": bool(write_result["byte_cap_reached"]),
        "truncated_reason": "max_bytes_per_dataset" if write_result["byte_cap_reached"] else "",
        "records_jsonl": str(records_path.resolve()),
        "candidate": item,
    }


def _kaggle_credentials() -> tuple[str, str]:
    username = os.getenv("KAGGLE_USERNAME", "").strip()
    key = (os.getenv("KAGGLE_KEY", "") or os.getenv("KAGGLE_API_TOKEN", "")).strip()
    if not username or not key:
        raise ObtainerCliError(
            "KAGGLE_CREDENTIALS_MISSING",
            "Kaggle download requires KAGGLE_USERNAME and KAGGLE_KEY",
            hint="Set Kaggle credentials in the environment; never put them in a manifest.",
        )
    return username, key


def _download_kaggle_archive(
    dataset_id: str,
    destination: Path,
    *,
    username: str,
    key: str,
    max_bytes: int,
) -> int:
    import httpx

    url = f"https://www.kaggle.com/api/v1/datasets/download/{dataset_id}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with httpx.Client(
            auth=(username, key),
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=20.0),
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if max_bytes > 0 and written > max_bytes:
                            raise ObtainerCliError(
                                "KAGGLE_ARCHIVE_TOO_LARGE",
                                f"Kaggle archive exceeded {max_bytes} bytes",
                            )
                        handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return written


def _safe_zip_member(name: str) -> str:
    normalized = str(name or "").replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ObtainerCliError("KAGGLE_UNSAFE_ARCHIVE", f"unsafe Kaggle archive member: {name!r}")
    return normalized


def _iter_kaggle_member(archive: zipfile.ZipFile, member: str) -> Iterable[dict[str, Any]]:
    suffix = Path(member).suffix.lower()
    with archive.open(member, "r") as raw:
        if suffix in {".jsonl", ".ndjson"}:
            for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                if not line.strip():
                    continue
                value = json.loads(line)
                yield value if isinstance(value, dict) else {"value": value}
            return
        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"), delimiter=delimiter)
            for row in reader:
                yield dict(row)
            return
        if suffix == ".json":
            value = json.load(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
            values = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), list) else value
            if isinstance(values, list):
                for item in values:
                    yield item if isinstance(item, dict) else {"value": item}
            elif isinstance(values, dict):
                yield values
            return
        if suffix == ".parquet":
            payload = raw.read()
            try:
                import pyarrow.parquet as parquet
            except ImportError as exc:
                raise ObtainerCliError(
                    "KAGGLE_PARQUET_DEPENDENCY_MISSING",
                    "reading Kaggle parquet files requires pyarrow",
                    hint="Install pyarrow or select a Kaggle dataset containing CSV/JSONL files.",
                ) from exc
            table = parquet.read_table(io.BytesIO(payload))
            for row in table.to_pylist():
                yield dict(row)


def _export_kaggle_jsonl(
    *,
    item: dict[str, Any],
    output_root: Path,
    split: str,
    max_rows: int,
    max_bytes_per_dataset: int,
) -> dict[str, Any]:
    dataset_id = str(
        item.get("dataset_id") or (item.get("download") or {}).get("dataset_id") or ""
    ).strip()
    if not dataset_id:
        return {"ok": False, "status": "failed", "source": "kaggle", "error": "missing dataset_id", "candidate": item}
    username, key = _kaggle_credentials()
    effective_max_rows = _effective_max_rows(max_rows)
    effective_max_bytes = _effective_max_bytes(max_bytes_per_dataset)
    archive_path = output_root / "archives" / f"{_safe_name(dataset_id)}.zip"
    archive_bytes = _download_kaggle_archive(
        dataset_id,
        archive_path,
        username=username,
        key=key,
        max_bytes=max(MAX_BYTES_PER_DATASET, effective_max_bytes),
    )
    supported = {".jsonl", ".ndjson", ".json", ".csv", ".tsv", ".parquet"}
    dataset_name = _safe_name(dataset_id)
    records_path = output_root / "records" / f"{dataset_name}.{split}.jsonl"

    def rows() -> Iterable[dict[str, Any]]:
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                _safe_zip_member(info.filename)
                for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in supported
            ]
            if not members:
                raise ObtainerCliError(
                    "KAGGLE_NO_TABULAR_FILE",
                    f"Kaggle dataset {dataset_id!r} contains no supported JSON/CSV/Parquet file",
                )
            emitted = 0
            for member in members:
                for row_index, row in enumerate(_iter_kaggle_member(archive, member), 1):
                    yield _normalize_row(
                        row,
                        dataset_id=dataset_id,
                        split=split,
                        row_index=emitted + 1,
                        source_domain="kaggle",
                        source_uri_prefix=f"kaggle://datasets/{dataset_id}/{member}#{row_index}",
                    )
                    emitted += 1
                    if emitted >= effective_max_rows:
                        return

    write_result = _write_jsonl(records_path, rows(), max_bytes=effective_max_bytes)
    rows_written = int(write_result["rows_written"])
    return {
        "ok": rows_written > 0,
        "status": "completed" if rows_written > 0 else "empty",
        "source": "kaggle",
        "dataset_id": dataset_id,
        "split": split,
        "archive": str(archive_path.resolve()),
        "archive_bytes": archive_bytes,
        "rows_written": rows_written,
        "bytes_written": int(write_result["bytes_written"]),
        "max_rows_requested": max_rows,
        "max_rows_effective": effective_max_rows,
        "max_bytes_requested": max_bytes_per_dataset,
        "max_bytes_effective": effective_max_bytes,
        "truncated": bool(write_result["byte_cap_reached"]),
        "truncated_reason": "max_bytes_per_dataset" if write_result["byte_cap_reached"] else "",
        "records_jsonl": str(records_path.resolve()),
        "candidate": item,
    }


def _effective_max_rows(max_rows: int) -> int:
    if max_rows <= 0:
        return MAX_ROWS_PER_DATASET
    return min(max_rows, MAX_ROWS_PER_DATASET)


def _effective_max_bytes(max_bytes: int) -> int:
    if max_bytes <= 0:
        return MAX_BYTES_PER_DATASET
    return min(max_bytes, MAX_BYTES_PER_DATASET)


def _write_download_progress(
    path: Path,
    *,
    total: int,
    index: int,
    item: dict[str, Any] | None,
    results: list[dict[str, Any]],
    state: str,
) -> None:
    completed = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]
    current_dataset = None
    if item:
        current_dataset = str(
            item.get("dataset_id")
            or (item.get("download") or {}).get("dataset_id")
            or ""
        ) or None
    payload = {
        "schema_version": "obtainercli.download.progress.v1",
        "updated_at": utc_now(),
        "state": state,
        "total": total,
        "processed": len(results),
        "current_index": index,
        "current_dataset": current_dataset,
        "completed": len(completed),
        "failed": len(failed),
        "percent": int((len(results) / total) * 100) if total else 100,
        "last_result": results[-1] if results else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def download_manifest(
    *,
    manifest: str | Path,
    output_root: str | Path,
    limit: int = 0,
    split: str = "train",
    max_rows: int = MAX_ROWS_PER_DATASET,
    max_bytes_per_dataset: int = MAX_BYTES_PER_DATASET,
    streaming: bool = True,
) -> dict[str, Any]:
    if max_rows < 0:
        raise ObtainerCliError(
            "INVALID_MAX_ROWS",
            "download max rows must be zero or positive",
            hint=f"Use a value from 1 to {MAX_ROWS_PER_DATASET}; 0 is capped to the default per-dataset limit.",
        )
    if max_bytes_per_dataset < 0:
        raise ObtainerCliError(
            "INVALID_MAX_BYTES",
            "download max bytes per dataset must be zero or positive",
            hint=f"Use a value from 1 to {MAX_BYTES_PER_DATASET}; 0 is capped to the default per-dataset byte limit.",
        )

    payload = _read_manifest(manifest)
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    items = _iter_download_items(payload, limit=limit)
    progress_path = output_path / "download_progress.json"

    results: list[dict[str, Any]] = []
    _write_download_progress(progress_path, total=len(items), index=0, item=None, results=results, state="starting")
    for index, item in enumerate(items, 1):
        _write_download_progress(progress_path, total=len(items), index=index, item=item, results=results, state="running")
        source = str(item.get("source") or (item.get("download") or {}).get("method") or "").lower()
        if source == "huggingface":
            try:
                results.append(
                    _export_huggingface_jsonl(
                        item=item,
                        output_root=output_path,
                        split=split,
                        max_rows=max_rows,
                        max_bytes_per_dataset=max_bytes_per_dataset,
                        streaming=streaming,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "status": "failed",
                        "source": "huggingface",
                        "dataset_id": item.get("dataset_id"),
                        "error": str(exc),
                        "candidate": item,
                    }
                )
        elif source == "kaggle":
            try:
                results.append(
                    _export_kaggle_jsonl(
                        item=item,
                        output_root=output_path,
                        split=split,
                        max_rows=max_rows,
                        max_bytes_per_dataset=max_bytes_per_dataset,
                    )
                )
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "status": "failed",
                        "source": "kaggle",
                        "dataset_id": item.get("dataset_id"),
                        "error": f"{type(exc).__name__}: {exc}",
                        "candidate": item,
                    }
                )
        else:
            results.append(
                {
                    "ok": False,
                    "status": "skipped",
                    "source": source or "unknown",
                    "dataset_id": item.get("dataset_id"),
                    "error": "unsupported download source",
                    "candidate": item,
                    }
                )
        _write_download_progress(progress_path, total=len(items), index=index, item=item, results=results, state="running")

    result_path = output_path / "download_results.json"
    completed = [row for row in results if row.get("ok")]
    response = {
        "ok": bool(completed),
        "status": "completed" if completed else "failed",
        "schema_version": "obtainercli.download.v1",
        "created_at": utc_now(),
        "manifest": str(Path(manifest).resolve()),
        "output_root": str(output_path.resolve()),
        "max_rows_requested": max_rows,
        "max_rows_effective": _effective_max_rows(max_rows),
        "max_rows_per_dataset": MAX_ROWS_PER_DATASET,
        "max_bytes_requested": max_bytes_per_dataset,
        "max_bytes_effective": _effective_max_bytes(max_bytes_per_dataset),
        "max_bytes_per_dataset": MAX_BYTES_PER_DATASET,
        "requested": len(items),
        "completed": len(completed),
        "results": results,
        "records_jsonl": [row["records_jsonl"] for row in completed if row.get("records_jsonl")],
    }
    result_path.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_download_progress(
        progress_path,
        total=len(items),
        index=len(items),
        item=items[-1] if items else None,
        results=results,
        state=response["status"],
    )
    return {
        **response,
        "result_path": str(result_path.resolve()),
        "progress_path": str(progress_path.resolve()),
    }
