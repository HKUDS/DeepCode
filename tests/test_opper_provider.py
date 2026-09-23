"""Pin the Opper provider as a mirror of the Requesty provider.

Opper is an EU-hosted OpenAI-compatible gateway wired on the same generic
``openai_compat`` path as OpenRouter and Requesty. These tests assert the
shared gateway wiring, pin the Opper-specific base URL and env var, and lock
the one place Opper differs from the other gateways: its ids are bare pool
names, but a ``provider/model`` id pins a single route, so the prefix must not
be stripped the way Forge's is.
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

from core.providers.registry import find_by_name  # noqa: E402

OPPER = find_by_name("opper")
REQUESTY = find_by_name("requesty")
FORGE = find_by_name("forge")


# ---- registry --------------------------------------------------------------


def test_opper_is_registered() -> None:
    assert OPPER is not None
    assert OPPER.name == "opper"
    assert OPPER.display_name == "Opper"


def test_opper_mirrors_the_generic_gateway_wiring() -> None:
    assert OPPER is not None and REQUESTY is not None
    assert OPPER.backend == REQUESTY.backend == "openai_compat"
    assert OPPER.is_gateway is True
    assert OPPER.endpoint_class == "gateway"
    assert OPPER.is_local is False
    assert OPPER.is_oauth is False


def test_opper_provider_specific_endpoint() -> None:
    assert OPPER is not None
    assert OPPER.default_api_base == "https://api.opper.ai/v3/compat"
    assert OPPER.env_key == "OPPER_API_KEY"
    assert OPPER.detect_by_base_keyword == "opper.ai"


def test_opper_does_not_borrow_another_gateways_key_prefix() -> None:
    # OpenRouter keys start with ``sk-or-``; Opper keys carry no such marker,
    # so the prefix heuristic must stay empty.
    assert OPPER is not None
    assert OPPER.detect_by_key_prefix == ""


def test_opper_sends_anthropic_cache_control() -> None:
    # Opper serves Anthropic models and prices cached input separately, so the
    # cache-control markers apply, as they do for the other gateways.
    assert OPPER is not None
    assert OPPER.supports_prompt_caching is True


def test_opper_keeps_a_pinned_route_prefix() -> None:
    """Unlike Forge, a ``provider/`` prefix on an Opper id is meaningful.

    Opper ids are bare pool names such as ``claude-sonnet-4-6``, where a pool
    is every provider serving that model. ``azure/gpt-5.5`` pins one provider
    or region instead, so stripping the prefix would silently change routing.
    """
    assert OPPER is not None and FORGE is not None
    assert FORGE.strip_model_prefix is True
    assert OPPER.strip_model_prefix is False
