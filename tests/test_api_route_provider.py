"""API Route uses the shared OpenAI-compatible gateway path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ConfigError, DeepCodeConfig, make_llm_provider
from core.providers.openai_compat import OpenAICompatProvider
from core.providers.registry import find_by_name


def _config(api_key: str | None) -> DeepCodeConfig:
    return DeepCodeConfig.model_validate(
        {
            "agents": {"defaults": {"provider": "api_route", "model": "gpt-5.5"}},
            "providers": {"api_route": {"apiKey": api_key}},
        }
    )


def test_api_route_is_a_named_gateway() -> None:
    spec = find_by_name("api_route")

    assert spec is not None
    assert spec.display_name == "API Route"
    assert spec.env_key == "API_ROUTE_API_KEY"
    assert spec.default_api_base == "https://global.api-route.com/v1"
    assert spec.backend == "openai_compat"
    assert spec.resolve_endpoint_class(spec.default_api_base) == "gateway"
    assert spec.resolve_endpoint_class("https://other.example/v1") == "unknown"
    assert spec.detect_by_key_prefix == ""
    assert spec.strip_model_prefix is False


def test_api_route_connection_uses_its_key_and_default_endpoint() -> None:
    provider = make_llm_provider(_config("test-api-route-key"))

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "test-api-route-key"
    assert provider.api_base == "https://global.api-route.com/v1"
    assert provider.default_model == "gpt-5.5"


def test_api_route_requires_its_own_key() -> None:
    with pytest.raises(ConfigError, match=r"providers\.api_route\.apiKey"):
        make_llm_provider(_config(None))
