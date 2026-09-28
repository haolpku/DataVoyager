"""Model pool (L4 config) -- a registry of LLM endpoints that operators bind to.

Persisted per-warehouse at ``<root>/models.json``. Register a model once with
``datamixer model add`` and reference it by name from any LLM operator
(``--arg model=<name>``). Required fields mirror the brief: name, note, api_url,
api_key, response_format, model; everything else has a default.

API keys may be stored literally or as ``env:VARNAME`` (resolved from the
environment at call time, so secrets need not be written to disk).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# response_format selects the request/response wire shape the LLM client uses.
RESPONSE_FORMATS = ("openaichat", "response")

# Defaults for the optional knobs ("其他参数要有缺省值").
DEFAULTS = {
    "temperature": 0.0,
    "max_tokens": 40000,
    "timeout": 60,
    "max_concurrency": 64,
    "top_p": 1.0,
}


@dataclass
class ModelSpec:
    name: str
    api_url: str
    api_key: str = ""
    response_format: str = "openaichat"
    model: str = ""
    note: str = ""
    temperature: float = DEFAULTS["temperature"]
    max_tokens: int = DEFAULTS["max_tokens"]
    timeout: int = DEFAULTS["timeout"]
    max_concurrency: int = DEFAULTS["max_concurrency"]
    top_p: float = DEFAULTS["top_p"]
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.response_format not in RESPONSE_FORMATS:
            raise ValueError(
                f"response_format must be one of {RESPONSE_FORMATS}, "
                f"got {self.response_format!r}")
        if not self.model:
            self.model = self.name

    def resolved_key(self) -> str:
        """Resolve ``env:VAR`` references at call time."""
        if self.api_key.startswith("env:"):
            return os.environ.get(self.api_key[4:], "")
        return self.api_key

    def masked(self) -> dict:
        d = asdict(self)
        k = self.api_key
        if k and not k.startswith("env:"):
            d["api_key"] = (k[:4] + "***") if len(k) > 4 else "***"
        return d


def _system_model_pool():
    """Return the Starter (system) model pool, or None when not configured."""
    from dataflowwebagent.schema.model_pool import StarterModelPool, load_starter_system_config_sync

    package_root = Path(__file__).resolve().parents[3]
    system = load_starter_system_config_sync(package_root, prefer_db=False)
    model_value = system.get("model")
    if not (isinstance(model_value, dict) and isinstance(model_value.get("pool"), list)):
        return None
    pool = StarterModelPool(system)
    return pool if pool.entries else None


def _standalone_model_spec(name: str) -> "ModelSpec | None":
    """Resolve the package-owned flat Codex config for direct DataMixer use.

    Managed acquisition registers this provider in ``models.json``.  Direct
    WebAgent commands should also work from an empty warehouse, so they get a
    transient spec from ``configs/dataflowwebagent.yaml`` and its env refs.
    """
    from dataflowwebagent.schema.model_pool import load_starter_system_config_sync, responses_url
    from dataflowwebagent.schema.system_runtime import resolve_runtime_model_config

    package_root = Path(__file__).resolve().parents[3]
    system = load_starter_system_config_sync(package_root, prefer_db=False)
    runtime = resolve_runtime_model_config(system)
    if not runtime.ready:
        return None
    return ModelSpec(
        name=name,
        api_url=responses_url(runtime.base_url),
        api_key=runtime.api_key,
        response_format="response",
        model=runtime.model,
        note="Resolved from the standalone DataflowWebAgent configuration.",
    )


def system_default_model_name() -> str:
    """Name of the Starter model-pool default model for pipeline LLM operators."""
    pool = _system_model_pool()
    if pool is None:
        return "codex" if _standalone_model_spec("codex") is not None else ""
    entry = pool.default_entry()
    return entry.name if entry else ""


def resolve_from_system_pool(name: str) -> "ModelSpec | None":
    """Resolve a Starter (system) model-pool entry into a proxy-routed ModelSpec.

    The DataMixer warehouse ``models.json`` is a thin registry; the
    authoritative endpoints/keys live in the system model pool and are applied
    at call time by the LLM client.  This fallback lets any system pool name be
    used (webagent kernel, expander, pipeline LLM operators) without a separate
    warehouse registration.
    """
    from dataflowwebagent.schema.model_pool import responses_url

    pool = _system_model_pool()
    if pool is None:
        return None
    entry = pool.get_entry_by_name(name)
    if entry is None or not entry.enabled:
        return None
    provider = pool.resolve_proxy_provider(entry.name, tier=entry.tier)
    if provider is None:
        return None
    return ModelSpec(
        name=entry.name,
        api_url=responses_url(str(provider.base_url or "")),
        api_key=str(provider.api_key or ""),
        response_format="response",
        model=str(entry.model_name),
        max_tokens=DEFAULTS["max_tokens"],
    )


class ModelPool:
    def __init__(self, root):
        self.path = Path(root) / "models.json"
        self._models: dict[str, dict] = {}
        self._default_model = ""
        if self.path.exists():
            payload = json.loads(self.path.read_text())
            self._models = payload.get("models", {})
            self._default_model = str(payload.get("default_model") or "")

    def _save(self):
        self.path.write_text(
            json.dumps(
                {"default_model": self._default_model, "models": self._models},
                ensure_ascii=False,
                indent=2,
            ))

    def add(self, spec: ModelSpec) -> None:
        self._models[spec.name] = asdict(spec)
        self._save()

    def set_default(self, name: str) -> None:
        if name not in self._models:
            raise KeyError(
                f"model {name!r} not in pool (have: {sorted(self._models)})"
            )
        self._default_model = name
        self._save()

    def default_name(self) -> str:
        if self._default_model in self._models:
            return self._default_model
        return "codex" if _standalone_model_spec("codex") is not None else ""

    def get(self, name: str) -> ModelSpec:
        if name not in self._models:
            resolved = resolve_from_system_pool(name)
            if resolved is not None:
                return resolved
            if str(name or "").strip().lower() in {"codex", "default"}:
                resolved = _standalone_model_spec(str(name).strip() or "codex")
                if resolved is not None:
                    return resolved
            raise KeyError(
                f"model {name!r} not in warehouse pool (have: "
                f"{sorted(self._models)}) and not in the Starter model pool")
        return ModelSpec(**self._models[name])

    def remove(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            if self._default_model == name:
                self._default_model = ""
            self._save()
            return True
        return False

    def names(self) -> list[str]:
        return sorted(self._models)

    def list(self) -> list[dict]:
        return [ModelSpec(**v).masked() for v in self._models.values()]
