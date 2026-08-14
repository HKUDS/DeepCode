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
