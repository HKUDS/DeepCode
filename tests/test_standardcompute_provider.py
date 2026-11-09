"""Pin the Standard Compute provider as a mirror of the Opper provider.

Standard Compute is an OpenAI-compatible gateway wired on the same generic
``openai_compat`` path as OpenRouter, Requesty and Opper. These tests assert
the shared gateway wiring, pin the Standard Compute base URL and env var, and
lock the two places it differs: ids are ``vendor/model`` so the prefix must
survive, and the gateway places its own cache markers, so DeepCode does not
send them.
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

STANDARDCOMPUTE = find_by_name("standardcompute")
OPPER = find_by_name("opper")
FORGE = find_by_name("forge")


# ---- registry --------------------------------------------------------------


def test_standardcompute_is_registered() -> None:
    assert STANDARDCOMPUTE is not None
    assert STANDARDCOMPUTE.name == "standardcompute"
    assert STANDARDCOMPUTE.display_name == "Standard Compute"


def test_standardcompute_mirrors_the_generic_gateway_wiring() -> None:
    assert STANDARDCOMPUTE is not None and OPPER is not None
    assert STANDARDCOMPUTE.backend == OPPER.backend == "openai_compat"
    assert STANDARDCOMPUTE.is_gateway is True
    assert STANDARDCOMPUTE.endpoint_class == "gateway"
    assert STANDARDCOMPUTE.is_local is False
    assert STANDARDCOMPUTE.is_oauth is False


def test_standardcompute_provider_specific_endpoint() -> None:
    assert STANDARDCOMPUTE is not None
    assert STANDARDCOMPUTE.default_api_base == "https://api.stdcmpt.com/v1"
    assert STANDARDCOMPUTE.env_key == "STANDARDCOMPUTE_API_KEY"
    assert STANDARDCOMPUTE.detect_by_base_keyword == "stdcmpt.com"


def test_standardcompute_does_not_borrow_another_gateways_key_prefix() -> None:
    assert STANDARDCOMPUTE is not None
    assert STANDARDCOMPUTE.detect_by_key_prefix == ""


def test_standardcompute_leaves_cache_markers_to_the_gateway() -> None:
    # The gateway places its own cache-control markers on Claude requests.
    assert STANDARDCOMPUTE is not None
    assert STANDARDCOMPUTE.supports_prompt_caching is False


def test_standardcompute_keeps_the_vendor_prefix() -> None:
    """Unlike Forge, the ``vendor/`` prefix is part of the id.

    ``StandardCompute`` is the routed default; pinned ids look like
    ``anthropic/claude-opus-5.5``, so stripping the prefix would break them.
    """
    assert STANDARDCOMPUTE is not None and FORGE is not None
    assert FORGE.strip_model_prefix is True
    assert STANDARDCOMPUTE.strip_model_prefix is False
