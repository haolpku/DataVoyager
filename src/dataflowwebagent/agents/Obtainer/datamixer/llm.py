"""Minimal, dependency-free LLM client supporting two wire formats.

Selected per-model via ``ModelSpec.response_format``:

* ``openaichat`` -- OpenAI **Chat Completions** shape: POST ``{messages: [...]}``
  to the chat-completions URL; read ``choices[0].message.content``.
* ``response``   -- OpenAI **Responses API** shape: POST ``{input: [...]}`` to the
  responses URL; read ``output_text`` (or assemble it from ``output[].content``).

The same ``messages`` list (``[{role, content}, ...]``) is accepted for both, so
an operator is written once and the format is a pure config switch.

Tests and offline runs override :data:`complete` (e.g. monkeypatch) so no network
is required.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from dataflowwebagent.schema.model_pool import (
    StarterModelPool,
    chat_completions_url,
    load_starter_system_config_sync,
    responses_url,
)
from .models import ModelSpec
from .telemetry import meter_for


def _post(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM connection error: {e.reason}") from None


def _chat_payload(spec: ModelSpec, messages, json_mode: bool) -> dict:
    p = {
        "model": spec.model,
        "messages": messages,
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "top_p": spec.top_p,
        **spec.extra,
    }
    if json_mode:
        p["response_format"] = {"type": "json_object"}
    return {key: value for key, value in p.items() if value is not None}


def _responses_payload(spec: ModelSpec, messages, json_mode: bool) -> dict:
    p = {
        "model": spec.model,
        "input": messages,
        "temperature": spec.temperature,
        "max_output_tokens": spec.max_tokens,
        "top_p": spec.top_p,
        **spec.extra,
    }
    if json_mode:
        p["text"] = {"format": {"type": "json_object"}}
    return {key: value for key, value in p.items() if value is not None}


def _extract_chat(resp: dict) -> str:
    return resp["choices"][0]["message"]["content"]


def _extract_responses(resp: dict) -> str:
    # Reasoning models (e.g. DeepSeek) can spend the whole token budget on
    # reasoning, so the upstream returns finish_reason=length with an empty or
    # partially-truncated message. That is transient (reasoning length varies
    # per attempt): surface it as a retryable error instead of returning a
    # truncated payload or crashing the JSON parser.
    if str(resp.get("status") or "") == "incomplete":
        raise RuntimeError(
            "LLM response incomplete (max_output_tokens reached before a complete output)"
        )
    if resp.get("output_text"):
        return resp["output_text"]
    parts = []
    for item in resp.get("output", []):
        for c in item.get("content", []):
            if isinstance(c, dict) and c.get("text"):
                parts.append(c["text"])
    if parts:
        return "".join(parts)
    raise RuntimeError(f"could not parse responses output: {str(resp)[:200]}")


def safe_error_summary(exc: BaseException) -> str:
    """Return a useful provider diagnostic without persisting provider bodies."""
    text = str(exc)
    http = next((token.strip(":,;()[]") for token in text.split()
                 if token.strip(":,;()[]").isdigit() and len(token.strip(":,;()[]")) == 3), None)
    if "LLM HTTP" in text and http:
        return f"LLM HTTP {http}"
    if "LLM connection error" in text:
        return "LLM connection error"
    if "incomplete" in text.casefold():
        return "LLM response incomplete"
    return f"{type(exc).__name__}: {text[:120]}"


def _call(spec: ModelSpec, messages, json_mode: bool) -> str:
    spec = _proxy_spec_if_available(spec)
    if spec.response_format not in {"openaichat", "response"}:
        raise ValueError(f"unknown response_format: {spec.response_format!r}")
    meter = meter_for(spec.telemetry_key)
    call_id = meter.start() if meter else 0
    started = time.monotonic()
    response = None
    failed = True
    error_summary = ""
    try:
        payload = (_chat_payload if spec.response_format == "openaichat" else _responses_payload)(spec, messages, json_mode)
        response = _post(spec.api_url, payload, spec.resolved_key(), spec.timeout)
        result = (_extract_chat if spec.response_format == "openaichat" else _extract_responses)(response)
        failed = False
        return result
    except Exception as exc:
        # Persist only a stable diagnostic category. Provider bodies can echo
        # prompts or secrets, so they must not enter run artifacts.
        error_summary = safe_error_summary(exc)
        raise
    finally:
        if meter:
            meter.finish(call_id, spec.model, response, failed, time.monotonic() - started, error_summary)


def _proxy_spec_if_available(spec: ModelSpec) -> ModelSpec:
    package_root = Path(__file__).resolve().parents[3]
    system = load_starter_system_config_sync(package_root, prefer_db=False)
    model_value = system.get("model")
    has_explicit_pool = (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )
    if not has_explicit_pool:
        return spec
    provider = StarterModelPool(system).resolve_proxy_provider(spec.name or spec.model)
    if provider is None:
        return spec
    api_url = responses_url(provider.base_url) if spec.response_format == "response" else chat_completions_url(provider.base_url)
    return replace(
        spec,
        api_url=api_url,
        api_key=provider.api_key,
        model=provider.model,
    )


def _retryable(msg: str) -> bool:
    return ("connection error" in msg or "timed out" in msg
            or "HTTP 429" in msg or "HTTP 5" in msg
            or "incomplete" in msg or "max_output_tokens" in msg)


def complete(spec: ModelSpec, messages, json_mode: bool = True,
             max_retries: int = 3, base_delay: float = 0.5) -> str:
    """Call the model and return the text content (format per ``spec``), with
    exponential backoff on transient errors (429 / 5xx / connection / timeout)."""
    import random
    import time
    last = None
    for attempt in range(max_retries + 1):
        try:
            return _call(spec, messages, json_mode)
        except ValueError:
            raise                      # config error -> don't retry
        except RuntimeError as e:
            last = e
            if attempt < max_retries and _retryable(str(e)):
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 0.2))
                continue
            raise
    raise last  # pragma: no cover


def parse_json(text: str):
    """Tolerantly extract a JSON object from a model's text output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"no JSON object in model output: {text[:200]}")
