"""Pin the FutureInfra provider as a mirror of the Opper provider.

FutureInfra's AI router is an OpenAI-compatible gateway wired on the same
generic ``openai_compat`` path as OpenRouter, Requesty and Opper. These tests
assert the shared gateway wiring, pin the FutureInfra-specific base URL and env
var, and lock that its ``provider/model`` ids keep their prefix rather than
being stripped the way Forge's are.
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

FUTUREINFRA = find_by_name("futureinfra")
OPPER = find_by_name("opper")
FORGE = find_by_name("forge")


# ---- registry --------------------------------------------------------------


def test_futureinfra_is_registered() -> None:
    assert FUTUREINFRA is not None
    assert FUTUREINFRA.name == "futureinfra"
    assert FUTUREINFRA.display_name == "FutureInfra"


def test_futureinfra_has_a_config_block() -> None:
    assert "futureinfra" in ProvidersConfig.model_fields


def test_futureinfra_mirrors_the_generic_gateway_wiring() -> None:
    assert FUTUREINFRA is not None and OPPER is not None
    assert FUTUREINFRA.backend == OPPER.backend == "openai_compat"
    assert FUTUREINFRA.is_gateway is True
    assert FUTUREINFRA.endpoint_class == "gateway"
    assert FUTUREINFRA.is_local is False
    assert FUTUREINFRA.is_oauth is False


def test_futureinfra_provider_specific_endpoint() -> None:
    assert FUTUREINFRA is not None
    assert FUTUREINFRA.default_api_base == "https://futureinfra.ai/v1/ai"
    assert FUTUREINFRA.env_key == "FUTUREINFRA_API_KEY"
    assert FUTUREINFRA.detect_by_base_keyword == "futureinfra.ai"


def test_futureinfra_default_endpoint_classifies_as_gateway() -> None:
    assert FUTUREINFRA is not None
    assert FUTUREINFRA.resolve_endpoint_class(FUTUREINFRA.default_api_base) == "gateway"


def test_futureinfra_does_not_borrow_another_gateways_key_prefix() -> None:
    # OpenRouter keys start with ``sk-or-``; the prefix heuristic stays empty.
    assert FUTUREINFRA is not None
    assert FUTUREINFRA.detect_by_key_prefix == ""


def test_futureinfra_keeps_the_provider_prefix() -> None:
    """Unlike Forge, FutureInfra ids are ``provider/model`` slugs.

    ``openai/gpt-4o-mini`` is the id the router expects, so stripping the
    prefix would send an id it does not serve.
    """
    assert FUTUREINFRA is not None and FORGE is not None
    assert FORGE.strip_model_prefix is True
    assert FUTUREINFRA.strip_model_prefix is False
