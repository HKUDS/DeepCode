"""Pin the Requesty provider as a mirror of the OpenRouter provider.

The Requesty router is an OpenAI-compatible gateway wired on the same generic
``openai_compat`` path as OpenRouter. These tests assert that the registry
entry mirrors OpenRouter where it should (backend, gateway flag, prompt
caching, ``provider/model`` naming) while pinning the Requesty-specific base
URL / env var, and that the model-catalog normalizer maps Requesty's
capability shape (``context_window`` + ``supports_*`` booleans) onto the same
fields the settings UI already consumes for OpenRouter.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BACKEND = ROOT / "new_ui" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.providers.registry import find_by_model, find_by_name  # noqa: E402

REQUESTY = find_by_name("requesty")
OPENROUTER = find_by_name("openrouter")


# ---- registry --------------------------------------------------------------


def test_requesty_is_registered() -> None:
    assert REQUESTY is not None
    assert REQUESTY.name == "requesty"
    assert REQUESTY.display_name == "Requesty"


def test_requesty_mirrors_openrouter_generic_wiring() -> None:
    assert OPENROUTER is not None and REQUESTY is not None
    # Same generic OpenAI-compatible gateway path as OpenRouter.
    assert REQUESTY.backend == OPENROUTER.backend == "openai_compat"
    assert REQUESTY.is_gateway is True
    assert REQUESTY.supports_prompt_caching is True
    assert REQUESTY.is_local is False
    assert REQUESTY.is_oauth is False


def test_requesty_provider_specific_endpoint() -> None:
    assert REQUESTY is not None
    assert REQUESTY.default_api_base == "https://router.requesty.ai/v1"
    assert REQUESTY.env_key == "REQUESTY_API_KEY"
    assert REQUESTY.detect_by_base_keyword == "requesty"


def test_requesty_does_not_borrow_openrouter_key_prefix() -> None:
    # OpenRouter keys start with ``sk-or-``; Requesty keys do not, so the
    # prefix heuristic must not be copied over.
    assert REQUESTY is not None
    assert REQUESTY.detect_by_key_prefix == ""


def test_requesty_shares_provider_slash_model_naming() -> None:
    # ``provider/model`` slugs resolve to the owning provider (openai/anthropic
    # /...), exactly like OpenRouter -- Requesty adds no new namespace.
    for model in ("openai/gpt-4o-mini", "anthropic/claude-sonnet-4-5"):
        spec = find_by_model(model)
        assert spec is not None
        assert spec.name in {"openai", "anthropic"}


# ---- model catalog normalization ------------------------------------------


def test_normalize_maps_requesty_capability_shape() -> None:
    from services.requesty_models import _normalize_model

    raw = {
        "id": "openai/gpt-4o-mini",
        "context_window": 128000,
        "max_output_tokens": 16384,
        "supports_tool_calling": True,
        "supports_reasoning": False,
        "supports_vision": True,
        "input_price": 0.00000015,
        "output_price": 0.0000006,
    }
    model = _normalize_model(raw, source="requesty")

    # context_window -> context_length
    assert model["context_length"] == 128000
    assert model["top_provider"]["context_length"] == 128000
    assert model["top_provider"]["max_completion_tokens"] == 16384
    # supports_* booleans -> supported_parameters array
    assert "tools" in model["supported_parameters"]
    assert "temperature" in model["supported_parameters"]
    assert "max_tokens" in model["supported_parameters"]
    assert "reasoning" not in model["supported_parameters"]
    # flat per-token prices -> nested pricing
    assert model["pricing"]["prompt"] == 0.00000015
    assert model["pricing"]["completion"] == 0.0000006
    assert model["source"] == "requesty"


def test_normalize_still_accepts_openrouter_shape() -> None:
    from services.requesty_models import _normalize_model

    raw = {
        "id": "z-ai/glm-5.1",
        "context_length": 65536,
        "supported_parameters": ["temperature", "max_tokens", "tools"],
        "pricing": {"prompt": "0.1", "completion": "0.2"},
    }
    model = _normalize_model(raw, source="seed")
    assert model["context_length"] == 65536
    assert model["supported_parameters"] == ["temperature", "max_tokens", "tools"]
    assert model["pricing"]["prompt"] == "0.1"


def test_seed_response_is_returned_without_key(monkeypatch) -> None:
    import services.requesty_models as rm

    monkeypatch.setattr(rm, "get_api_key", lambda _provider: None)
    monkeypatch.setattr(rm, "_read_cache", lambda: None)
    payload = rm.list_requesty_models()
    assert payload["source"] == "seed"
    assert payload["models"]
    assert all(m["id"] for m in payload["models"])
