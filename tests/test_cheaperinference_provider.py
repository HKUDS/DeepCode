"""Cheaper Inference gateway registration.

Cheaper Inference is an OpenAI-compatible gateway on the generic
``openai_compat`` backend. Like Forge, it resolves bare model ids.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import DeepCodeConfig, ProvidersConfig  # noqa: E402
from core.providers.credentials import CredentialStore  # noqa: E402
from core.providers.model_compat import resolve_model_compat  # noqa: E402
from core.providers.profiles import ConnectionResolver  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402


def test_cheaperinference_is_registered_as_a_gateway():
    spec = find_by_name("cheaperinference")
    assert spec is not None
    assert spec.display_name == "Cheaper Inference"
    assert spec.is_gateway is True
    assert spec.endpoint_class == "gateway"
    assert spec.backend == "openai_compat"
    assert spec.env_key == "CHEAPER_INFERENCE_API_KEY"
    assert spec.default_api_base == "https://api.cheaperinference.com/v1"


def test_cheaperinference_strips_the_vendor_prefix():
    """Cheaper Inference resolves bare model ids, like Forge."""

    spec = find_by_name("cheaperinference")
    assert spec.strip_model_prefix is True
    compat = resolve_model_compat(
        model_name="openai/gpt-5.4-mini", spec=spec, reasoning_effort=None
    )
    assert compat.model_name == "gpt-5.4-mini"


def test_cheaperinference_does_not_collide_with_other_gateway_detection():
    spec = find_by_name("cheaperinference")
    assert spec.detect_by_base_keyword == "cheaperinference.com"
    assert spec.detect_by_key_prefix == ""


def test_providers_config_exposes_cheaperinference():
    """``config.py`` reads providers via ``getattr(..., spec.name)``, so a
    missing field silently disables the provider everywhere."""

    assert hasattr(ProvidersConfig(), "cheaperinference")


def test_cheaperinference_profile_uses_the_default_base(tmp_path: Path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.set("my-ci", "ci-test-secret")
    config = DeepCodeConfig.model_validate(
        {"providers": {"profiles": {"my-ci": {"template": "cheaperinference"}}}}
    )
    connection = ConnectionResolver(config, credentials).resolve_connection("my-ci")

    assert connection.provider_name == "cheaperinference"
    assert connection.adapter == "openai_compat"
    assert connection.api_base == "https://api.cheaperinference.com/v1"
    assert connection.api_key == "ci-test-secret"
    assert connection.model_catalog == "openai"
    assert connection.is_usable is True
