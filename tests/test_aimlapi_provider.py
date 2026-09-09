"""AI/ML API gateway registration and its attribution headers.

The gateway is wired on the same generic ``openai_compat`` path as OpenRouter
and Requesty, so most of this file pins the registry entry against those two.

The attribution block gets more attention than the registry entry because it
fails *silently*: the gateway accepts a malformed partner id with a 200 and
simply records the traffic as untagged, so a typo is invisible at runtime and
only a shape assertion here can catch it. The same reasoning covers the origin
check — headers that ride a repointed base URL leak DeepCode's identity to a
third party without any error to notice.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import (  # noqa: E402
    DeepCodeConfig,
    ProviderConfig,
    ProvidersConfig,
)
from core.providers.catalog_service import _catalog_request, _parse_model  # noqa: E402
from core.providers.openai_compat import (  # noqa: E402
    _DEFAULT_AIMLAPI_HEADERS,
    OpenAICompatProvider,
    _uses_aimlapi_attribution,
)
from core.providers.profiles import ConnectionResolver  # noqa: E402
from core.providers.registry import find_by_model, find_by_name  # noqa: E402

AIMLAPI = find_by_name("aimlapi")
OPENROUTER = find_by_name("openrouter")

# apps/api gateway contract: /^part_[A-Za-z0-9]{1,64}$/ — no dashes, no
# underscores after the prefix.
PARTNER_ID_PATTERN = re.compile(r"^part_[A-Za-z0-9]{1,64}$")
# <channel>/<client>, channel from a closed enum, client [a-z0-9-]{1,32}.
SOURCE_PATTERN = re.compile(r"^(?:web|agent|mcp)/[a-z0-9-]{1,32}$")


# ---- registry --------------------------------------------------------------


def test_aimlapi_is_registered_as_a_gateway() -> None:
    assert AIMLAPI is not None
    assert AIMLAPI.name == "aimlapi"
    assert AIMLAPI.is_gateway is True
    assert AIMLAPI.backend == "openai_compat"


def test_aimlapi_display_name_is_the_vendor_spelling() -> None:
    # The vendor writes its own name lowercase and with the TLD; the label is
    # what every provider list renders, so it is pinned here.
    assert AIMLAPI is not None
    assert AIMLAPI.display_name == "aimlapi.com"
    assert AIMLAPI.label == "aimlapi.com"


def test_aimlapi_provider_specific_endpoint() -> None:
    assert AIMLAPI is not None
    assert AIMLAPI.default_api_base == "https://api.aimlapi.com/v1"
    assert AIMLAPI.env_key == "AIMLAPI_API_KEY"
    assert AIMLAPI.detect_by_base_keyword == "aimlapi"


def test_aimlapi_does_not_borrow_openrouter_key_prefix() -> None:
    # OpenRouter keys start with ``sk-or-``; AI/ML API keys do not, so the
    # prefix heuristic must not be copied over.
    assert AIMLAPI is not None
    assert AIMLAPI.detect_by_key_prefix == ""


def test_aimlapi_mirrors_openrouter_generic_wiring() -> None:
    """Unlike Forge, AI/ML API resolves ``vendor/model`` ids, and it honours
    ``cache_control`` markers (verified against the live gateway: a repeated
    prefix comes back with ``cached_tokens`` in usage)."""
    assert AIMLAPI is not None and OPENROUTER is not None
    assert AIMLAPI.strip_model_prefix is OPENROUTER.strip_model_prefix is False
    assert AIMLAPI.supports_prompt_caching is True
    assert AIMLAPI.is_local is False
    assert AIMLAPI.is_oauth is False


def test_aimlapi_shares_provider_slash_model_naming() -> None:
    # ``vendor/model`` slugs resolve to the owning vendor, exactly like
    # OpenRouter -- AI/ML API adds no new namespace.
    for model in ("openai/gpt-4o-mini", "anthropic/claude-sonnet-4.5"):
        spec = find_by_model(model)
        assert spec is not None
        assert spec.name in {"openai", "anthropic"}


def test_providers_config_exposes_aimlapi() -> None:
    """``config.py`` reads providers via ``getattr(..., spec.name)``, so a
    missing field silently disables the provider everywhere."""
    assert hasattr(ProvidersConfig(), "aimlapi")


# ---- attribution -----------------------------------------------------------


def test_partner_id_matches_the_gateway_contract() -> None:
    """A malformed id is accepted with a 200 and earns nothing — the failure
    mode is silence, so the shape is asserted rather than observed."""
    partner_id = _DEFAULT_AIMLAPI_HEADERS["X-AIMLAPI-Partner-ID"]
    assert PARTNER_ID_PATTERN.match(partner_id), partner_id


def test_source_matches_the_channel_contract() -> None:
    source = _DEFAULT_AIMLAPI_HEADERS["X-AIMLAPI-Source"]
    assert SOURCE_PATTERN.match(source), source


def test_referer_and_title_identify_deepcode_not_the_gateway() -> None:
    # OpenRouter convention: these name the calling application.
    assert _DEFAULT_AIMLAPI_HEADERS["X-Title"] == "DeepCode"
    assert "HKUDS/DeepCode" in _DEFAULT_AIMLAPI_HEADERS["HTTP-Referer"]


def test_attribution_is_scoped_to_our_own_origin() -> None:
    assert _uses_aimlapi_attribution(AIMLAPI, "https://api.aimlapi.com/v1")
    assert _uses_aimlapi_attribution(None, "https://api.aimlapi.com/v1")
    # A proxy that merely fronts the same API is somebody else's origin.
    assert not _uses_aimlapi_attribution(AIMLAPI, "https://proxy.example.com/v1")
    # ...and a lookalike domain must not satisfy the suffix test.
    assert not _uses_aimlapi_attribution(AIMLAPI, "https://api.aimlapi.com.evil.io/v1")
    assert not _uses_aimlapi_attribution(AIMLAPI, "https://notaimlapi.com/v1")


def test_headers_reach_the_client_without_clobbering_the_caller() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key",
        spec=AIMLAPI,
        extra_headers={"X-Title": "Mine", "X-Custom": "kept"},
    )
    sent = provider._client.default_headers
    assert sent["X-AIMLAPI-Partner-ID"] == "part_CxcOejScJ3hI0O2cExWchiBy"
    assert sent["X-AIMLAPI-Source"] == "agent/deepcode"
    # Merge, never assign: the caller's own value wins on a clash and their
    # unrelated headers survive.
    assert sent["X-Title"] == "Mine"
    assert sent["X-Custom"] == "kept"


def test_attribution_does_not_ride_other_providers() -> None:
    for name in ("openrouter", "requesty", "forge", "openai", "deepseek"):
        spec = find_by_name(name)
        assert spec is not None
        sent = OpenAICompatProvider(
            api_key="test-key", spec=spec
        )._client.default_headers
        assert "X-AIMLAPI-Partner-ID" not in sent, name
        assert "X-AIMLAPI-Source" not in sent, name


def test_the_shared_header_constant_is_never_mutated() -> None:
    """Each client gets a fresh dict; a per-instance override must not leak
    into the next connection built from the same template."""
    before = dict(_DEFAULT_AIMLAPI_HEADERS)
    OpenAICompatProvider(
        api_key="test-key", spec=AIMLAPI, extra_headers={"X-Title": "Mine"}
    )
    assert _DEFAULT_AIMLAPI_HEADERS == before
    second = OpenAICompatProvider(api_key="test-key", spec=AIMLAPI)
    assert second._client.default_headers["X-Title"] == "DeepCode"


# ---- model discovery -------------------------------------------------------


def _aimlapi_connection():
    config = DeepCodeConfig(
        providers=ProvidersConfig(aimlapi=ProviderConfig(api_key="test-key"))
    )
    return ConnectionResolver(config).resolve_connection("aimlapi")


def test_discovery_asks_for_the_chat_surface_only() -> None:
    """The directory serves every endpoint family the gateway hosts — image,
    video, speech and embedding rows included, with ids repeating across
    families. Unfiltered, the model picker offers rows a chat request
    rejects."""
    url, headers = _catalog_request(_aimlapi_connection())
    assert url == ("https://api.aimlapi.com/v1/models?type=openai%2Fchat-completions")
    assert headers["Authorization"] == "Bearer test-key"


def test_other_providers_keep_the_plain_models_url() -> None:
    config = DeepCodeConfig(
        providers=ProvidersConfig(requesty=ProviderConfig(api_key="test-key"))
    )
    connection = ConnectionResolver(config).resolve_connection("requesty")
    url, _ = _catalog_request(connection)
    assert url == "https://router.requesty.ai/v1/models"


def test_nested_metadata_is_read_when_the_flat_keys_are_absent() -> None:
    """AI/ML API nests the numbers under ``info``. Without this the picker
    shows a fallback 128k/8k for every model and the runtime budgets context
    against a window the model does not have."""
    model = _parse_model(
        {
            "id": "alibaba/glm-5.2",
            "type": "openai/chat-completions",
            "info": {
                "name": "GLM 5.2",
                "contextLength": 1000000,
                "outputMax": 131072,
            },
        }
    )
    assert model is not None
    assert model.name == "GLM 5.2"
    assert model.context_window == 1000000
    assert model.max_output_tokens == 131072


def test_flat_keys_still_win_over_nested_ones() -> None:
    model = _parse_model(
        {
            "id": "some/model",
            "name": "Flat",
            "context_length": 8192,
            "max_output_tokens": 512,
            "info": {"name": "Nested", "contextLength": 999, "outputMax": 99},
        }
    )
    assert model is not None
    assert (model.name, model.context_window, model.max_output_tokens) == (
        "Flat",
        8192,
        512,
    )


def test_provider_construction_never_mutates_the_environment(monkeypatch) -> None:
    """The key stays on the instance; nothing ambient learns it."""
    assert AIMLAPI is not None
    monkeypatch.delenv(AIMLAPI.env_key, raising=False)
    before = dict(os.environ)
    OpenAICompatProvider(api_key="fresh-key", spec=AIMLAPI)
    assert dict(os.environ) == before
    assert AIMLAPI.env_key not in os.environ
