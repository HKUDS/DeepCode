"""Forge gateway registration, plus the no-ambient-credential invariant.

Provider construction must never export a key to ``os.environ``: in a
long-lived App Server an ambient write lets every same-template connection
resolve another connection's key as "environment" — a cross-connection
credential bleed. The key travels only on the provider instance.
"""

from __future__ import annotations

import os
import sys
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


def test_provider_construction_never_mutates_the_environment(monkeypatch):
    """The key stays on the instance; nothing ambient learns it."""

    for name in ("forge", "openrouter", "zhipu", "openai"):
        spec = find_by_name(name)
        monkeypatch.delenv(spec.env_key, raising=False)
        before = dict(os.environ)
        OpenAICompatProvider(api_key="fresh-key", spec=spec)
        assert dict(os.environ) == before, f"{name} construction mutated os.environ"
        assert spec.env_key not in os.environ
