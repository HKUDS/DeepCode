"""Pin the Atlas Cloud provider as a mirror of the Requesty/Opper gateways.

Atlas Cloud is an OpenAI-compatible gateway wired on the generic
``openai_compat`` path. These tests assert the shared gateway wiring, pin the
Atlas Cloud base URL and env var, and lock the one place it differs from Forge:
model ids are ``vendor/model`` strings, so the prefix must not be stripped.
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

from core.config import ProvidersConfig  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402

ATLAS = find_by_name("atlascloud")
REQUESTY = find_by_name("requesty")
FORGE = find_by_name("forge")


def test_atlascloud_is_registered() -> None:
    assert ATLAS is not None
    assert ATLAS.name == "atlascloud"
    assert ATLAS.display_name == "Atlas Cloud"


def test_atlascloud_mirrors_the_generic_gateway_wiring() -> None:
    assert ATLAS is not None and REQUESTY is not None
    assert ATLAS.backend == REQUESTY.backend == "openai_compat"
    assert ATLAS.is_gateway is True
    assert ATLAS.endpoint_class == "gateway"
    assert ATLAS.is_local is False
    assert ATLAS.is_oauth is False


def test_atlascloud_provider_specific_endpoint() -> None:
    assert ATLAS is not None
    assert ATLAS.default_api_base == "https://api.atlascloud.ai/v1"
    assert ATLAS.env_key == "ATLASCLOUD_API_KEY"
    assert ATLAS.detect_by_base_keyword == "atlascloud"


def test_atlascloud_does_not_borrow_another_gateways_key_prefix() -> None:
    # OpenRouter keys start with ``sk-or-``; Atlas Cloud keys are ``apikey-...``
    # and must not be matched by the prefix heuristic.
    assert ATLAS is not None
    assert ATLAS.detect_by_key_prefix == ""


def test_atlascloud_keeps_the_vendor_prefix_on_model_ids() -> None:
    """``deepseek-ai/deepseek-v4-flash`` is one id; stripping ``deepseek-ai/`` breaks it."""
    assert ATLAS is not None and FORGE is not None
    assert FORGE.strip_model_prefix is True
    assert ATLAS.strip_model_prefix is False


def test_atlascloud_has_a_provider_config_block() -> None:
    assert "atlascloud" in ProvidersConfig.model_fields
