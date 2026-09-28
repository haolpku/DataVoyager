"""Configuration and model-pool contracts for the standalone web agent."""

from .model_pool import (
    DEFAULT_PROXY_API_KEY,
    DEFAULT_TIER,
    TIERS,
    ModelPoolEntry,
    ResolvedModelProvider,
    StarterModelPool,
    chat_completions_url,
    load_starter_system_config_sync,
    mask_secret,
    normalize_v1_base_url,
    normalize_wire_api,
    resolve_secret,
    responses_url,
)

__all__ = [
    "DEFAULT_PROXY_API_KEY",
    "DEFAULT_TIER",
    "TIERS",
    "ModelPoolEntry",
    "ResolvedModelProvider",
    "StarterModelPool",
    "chat_completions_url",
    "load_starter_system_config_sync",
    "mask_secret",
    "normalize_v1_base_url",
    "normalize_wire_api",
    "resolve_secret",
    "responses_url",
]
