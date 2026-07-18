"""Requesty model catalog helpers for the settings UI.

Mirror of :mod:`services.openrouter_models` for the Requesty router. The
Requesty ``/v1/models`` endpoint returns an OpenAI-shaped payload, but its
capability metadata differs from OpenRouter's: context size lives in
``context_window`` (not ``context_length``), capabilities are exposed as the
booleans ``supports_tool_calling`` / ``supports_reasoning`` /
``supports_vision`` (not a ``supported_parameters`` array), and prices are flat
per-token USD values. :func:`_normalize_model` maps those onto the same shape
the settings UI already consumes for OpenRouter. A small local cache keeps the
settings page usable when the router is slow or temporarily unavailable.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from settings import get_api_key


REQUESTY_MODELS_URL = "https://router.requesty.ai/v1/models"
CACHE_PATH = Path.home() / ".deepcode" / "cache" / "requesty_models.json"
CACHE_TTL_SECONDS = 24 * 60 * 60

SEED_MODELS: list[dict[str, Any]] = [
    {
        "id": "openai/gpt-4o-mini",
        "name": "GPT-4o mini",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 16384},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "openai/gpt-4o",
        "name": "GPT-4o",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 16384},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "anthropic/claude-sonnet-4-5",
        "name": "Claude Sonnet 4.5",
        "context_length": 200000,
        "top_provider": {"max_completion_tokens": 64000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "google/gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "context_length": 1048576,
        "top_provider": {"max_completion_tokens": 65536},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "deepseek/deepseek-chat",
        "name": "DeepSeek Chat",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 64000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
]


def list_requesty_models(
    *,
    supported_parameters: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return Requesty models from live API, cache, or curated seed data."""
    cached = _read_cache()
    if not force_refresh and cached and not _cache_expired(cached):
        return _filter_response(cached, supported_parameters=supported_parameters)

    api_key = get_api_key("requesty")
    if api_key:
        live = _fetch_live_models(api_key)
        if live is not None:
            _write_cache(live)
            return _filter_response(
                live, supported_parameters=supported_parameters
            )

    if cached:
        stale = dict(cached)
        stale["source"] = "cache"
        stale["stale"] = True
        return _filter_response(stale, supported_parameters=supported_parameters)

    return _seed_response(supported_parameters=supported_parameters)


def _fetch_live_models(api_key: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        REQUESTY_MODELS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None

    models = [
        _normalize_model(item, source="requesty") for item in payload.get("data", [])
    ]
    return {
        "models": sorted(models, key=lambda item: item["id"]),
        "source": "requesty",
        "cached_at": int(time.time()),
        "stale": False,
    }


def _read_cache() -> dict[str, Any] | None:
    try:
        if not CACHE_PATH.exists():
            return None
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            return None
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cache_expired(payload: dict[str, Any]) -> bool:
    cached_at = int(payload.get("cached_at") or 0)
    return cached_at <= 0 or time.time() - cached_at > CACHE_TTL_SECONDS


def _seed_response(*, supported_parameters: str | None = None) -> dict[str, Any]:
    payload = {
        "models": [_normalize_model(item, source="seed") for item in SEED_MODELS],
        "source": "seed",
        "cached_at": None,
        "stale": False,
    }
    return _filter_response(payload, supported_parameters=supported_parameters)


def _filter_response(
    payload: dict[str, Any],
    *,
    supported_parameters: str | None = None,
) -> dict[str, Any]:
    required = {
        item.strip() for item in (supported_parameters or "").split(",") if item.strip()
    }
    if not required:
        return payload
    models = [
        model
        for model in payload.get("models", [])
        if required.issubset(set(model.get("supported_parameters") or []))
    ]
    return {**payload, "models": models}


def _derive_supported_parameters(item: dict[str, Any]) -> list[str]:
    """Map Requesty's capability booleans onto OpenRouter-style parameter names.

    Requesty exposes ``supports_tool_calling`` / ``supports_reasoning`` /
    ``supports_vision`` booleans instead of a ``supported_parameters`` array.
    ``temperature`` and ``max_tokens`` are accepted by every chat model on the
    router, so they are always advertised.
    """
    params = ["temperature", "max_tokens"]
    if item.get("supports_tool_calling"):
        params.append("tools")
    if item.get("supports_reasoning"):
        params.append("reasoning")
    return params


def _normalize_model(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    # Requesty uses ``context_window``; fall back to ``context_length`` for the
    # seed rows and for any OpenRouter-shaped payloads.
    context_length = item.get("context_window")
    if context_length is None:
        context_length = item.get("context_length")

    top_provider = item.get("top_provider") or {}
    max_completion_tokens = top_provider.get("max_completion_tokens")
    if max_completion_tokens is None:
        max_completion_tokens = item.get("max_output_tokens")

    if "supported_parameters" in item:
        supported_parameters = list(item.get("supported_parameters") or [])
    else:
        supported_parameters = _derive_supported_parameters(item)

    # Requesty prices are flat per-token USD values (``input_price`` /
    # ``output_price``); OpenRouter nests them under ``pricing``.
    pricing = item.get("pricing") or {}
    prompt_price = pricing.get("prompt")
    if prompt_price is None:
        prompt_price = item.get("input_price")
    completion_price = pricing.get("completion")
    if completion_price is None:
        completion_price = item.get("output_price")

    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "context_length": context_length,
        "top_provider": {
            "context_length": context_length,
            "max_completion_tokens": max_completion_tokens,
            "is_moderated": top_provider.get("is_moderated"),
        },
        "supported_parameters": supported_parameters,
        "pricing": {
            "prompt": prompt_price,
            "completion": completion_price,
            "request": pricing.get("request"),
        },
        "expiration_date": item.get("expiration_date"),
        "source": source,
    }
