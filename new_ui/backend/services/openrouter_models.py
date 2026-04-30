"""OpenRouter model catalog helpers for the settings UI.

The OpenRouter ``/models`` endpoint is the source of truth for model ids such
as ``z-ai/glm-5.1``. A small local cache keeps the settings page usable when
OpenRouter is slow or temporarily unavailable.
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


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_PATH = Path.home() / ".deepcode" / "cache" / "openrouter_models.json"
CACHE_TTL_SECONDS = 24 * 60 * 60

SEED_MODELS: list[dict[str, Any]] = [
    {
        "id": "z-ai/glm-5.1",
        "name": "GLM 5.1",
        "context_length": 65536,
        "top_provider": {"max_completion_tokens": 40000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "anthropic/claude-sonnet-4.5",
        "name": "Claude Sonnet 4.5",
        "context_length": 200000,
        "top_provider": {"max_completion_tokens": 64000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "openai/gpt-5.1",
        "name": "GPT 5.1",
        "context_length": 272000,
        "top_provider": {"max_completion_tokens": 128000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "context_length": 1048576,
        "top_provider": {"max_completion_tokens": 65536},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
    {
        "id": "deepseek/deepseek-chat-v3.1",
        "name": "DeepSeek Chat V3.1",
        "context_length": 128000,
        "top_provider": {"max_completion_tokens": 64000},
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {},
        "expiration_date": None,
        "source": "seed",
    },
]


def list_openrouter_models(
    *,
    supported_parameters: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return OpenRouter models from live API, cache, or curated seed data."""
    cached = _read_cache()
    if not force_refresh and cached and not _cache_expired(cached):
        return _filter_response(cached, supported_parameters=supported_parameters)

    api_key = get_api_key("openrouter")
    if api_key:
        live = _fetch_live_models(
            api_key,
            supported_parameters=supported_parameters,
        )
        if live is not None:
            _write_cache(live)
            return live

    if cached:
        stale = dict(cached)
        stale["source"] = "cache"
        stale["stale"] = True
        return _filter_response(stale, supported_parameters=supported_parameters)

    return _seed_response(supported_parameters=supported_parameters)


def _fetch_live_models(
    api_key: str,
    *,
    supported_parameters: str | None = None,
) -> dict[str, Any] | None:
    query = {"output_modalities": "text"}
    if supported_parameters:
        query["supported_parameters"] = supported_parameters
    url = f"{OPENROUTER_MODELS_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
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

    models = [_normalize_model(item, source="openrouter") for item in payload.get("data", [])]
    return {
        "models": sorted(models, key=lambda item: item["id"]),
        "source": "openrouter",
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
        item.strip()
        for item in (supported_parameters or "").split(",")
        if item.strip()
    }
    if not required:
        return payload
    models = [
        model
        for model in payload.get("models", [])
        if required.issubset(set(model.get("supported_parameters") or []))
    ]
    return {**payload, "models": models}


def _normalize_model(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    top_provider = item.get("top_provider") or {}
    pricing = item.get("pricing") or {}
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "context_length": item.get("context_length"),
        "top_provider": {
            "context_length": top_provider.get("context_length"),
            "max_completion_tokens": top_provider.get("max_completion_tokens"),
            "is_moderated": top_provider.get("is_moderated"),
        },
        "supported_parameters": list(item.get("supported_parameters") or []),
        "pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request"),
        },
        "expiration_date": item.get("expiration_date"),
        "source": source,
    }
