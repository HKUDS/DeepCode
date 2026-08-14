"""Forge gateway registration, plus the gateway rule for ``env_extras``.

Ported from #116, which added Forge to the (since-removed) nanobot registry
and fixed the same ``setdefault`` bug in nanobot's provider. Core had that bug
half-fixed: ``spec.env_key`` was forced for gateways but ``env_extras`` was not.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import ProvidersConfig  # noqa: E402
from core.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402


def test_forge_is_registered_as_a_gateway():
    spec = find_by_name("forge")
    assert spec is not None
    assert spec.is_gateway is True
    assert spec.backend == "openai_compat"
    assert spec.env_key == "FORGE_API_KEY"
    assert spec.default_api_base == "https://api.forge.tensorblock.co/v1"


def test_forge_strips_the_vendor_prefix():
    """Unlike OpenRouter/Requesty, Forge resolves bare model ids."""

    forge = find_by_name("forge")
    openrouter = find_by_name("openrouter")
    assert forge.strip_model_prefix is True
    assert openrouter.strip_model_prefix is False


def test_forge_does_not_collide_with_other_gateway_detection():
    spec = find_by_name("forge")
    assert spec.detect_by_base_keyword == "forge.tensorblock.co"
    assert spec.detect_by_key_prefix == ""


def test_providers_config_exposes_forge():
    """``config.py`` reads providers via ``getattr(..., spec.name)``, so a
    missing field silently disables the provider everywhere."""

    assert hasattr(ProvidersConfig(), "forge")


def test_gateway_env_extras_override_ambient_values(monkeypatch):
    spec = replace(find_by_name("forge"), env_extras=(("FORGE_EXTRA", "{api_key}"),))
    monkeypatch.setenv("FORGE_EXTRA", "stale-from-shell")
    monkeypatch.delenv("FORGE_API_KEY", raising=False)

    OpenAICompatProvider(api_key="fresh-key", spec=spec)._setup_env("fresh-key", None)

    assert os.environ["FORGE_EXTRA"] == "fresh-key"


def test_direct_provider_env_extras_still_defer(monkeypatch):
    """Non-gateway providers keep deferring to the ambient environment."""

    spec = replace(find_by_name("zhipu"), env_extras=(("ZHIPU_EXTRA", "{api_key}"),))
    assert spec.is_gateway is False
    monkeypatch.setenv("ZHIPU_EXTRA", "set-by-user")
    monkeypatch.delenv(spec.env_key, raising=False)

    OpenAICompatProvider(api_key="fresh-key", spec=spec)._setup_env("fresh-key", None)

    assert os.environ["ZHIPU_EXTRA"] == "set-by-user"
