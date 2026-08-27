"""Tests for MiniMax (minimax) provider integration."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.registry import (
    PROVIDERS,
    find_by_model,
    find_by_name,
)

# ---------------------------------------------------------------------------
# Core registry tests
# ---------------------------------------------------------------------------


class TestCoreRegistry:
    """Tests for the core provider registry."""

    def test_find_by_name(self):
        spec = find_by_name("minimax")
        assert spec is not None
        assert spec.name == "minimax"
        assert spec.display_name == "MiniMax"

    def test_env_key(self):
        spec = find_by_name("minimax")
        assert spec is not None
        assert spec.env_key == "MINIMAX_API_KEY"

    def test_backend_is_openai_compat(self):
        spec = find_by_name("minimax")
        assert spec is not None
        assert spec.backend == "openai_compat"

    def test_default_api_base(self):
        spec = find_by_name("minimax")
        assert spec is not None
        assert spec.default_api_base == "https://api.minimax.io/v1"

    def test_find_by_model_minimax(self):
        spec = find_by_model("minimax/MiniMax-M3")
        assert spec is not None
        assert spec.name == "minimax"

    def test_find_by_model_minimax_m27(self):
        spec = find_by_model("minimax/MiniMax-M2.7")
        assert spec is not None
        assert spec.name == "minimax"

    def test_find_by_model_abab(self):
        spec = find_by_model("abab-7")
        assert spec is not None
        assert spec.name == "minimax"

    def test_not_gateway_or_local(self):
        spec = find_by_name("minimax")
        assert spec is not None
        assert spec.is_gateway is False
        assert spec.is_local is False

    def test_provider_in_registry_list(self):
        names = [s.name for s in PROVIDERS]
        assert "minimax" in names


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------


class TestConfigModel:
    """Tests for the config model with the new provider field."""

    def test_providers_config_has_minimax_field(self):
        from core.config import ProvidersConfig

        cfg = ProvidersConfig()
        assert hasattr(cfg, "minimax")
        assert cfg.minimax.api_key is None

    def test_providers_config_with_api_key(self):
        from core.config import ProviderConfig, ProvidersConfig

        cfg = ProvidersConfig(minimax=ProviderConfig(api_key="test-key"))
        assert cfg.minimax.api_key == "test-key"

    def test_providers_config_with_custom_base(self):
        from core.config import ProviderConfig, ProvidersConfig

        cfg = ProvidersConfig(
            minimax=ProviderConfig(
                api_key="test-key",
                api_base="https://api.minimaxi.com/v1",
            )
        )
        assert cfg.minimax.api_base == "https://api.minimaxi.com/v1"
