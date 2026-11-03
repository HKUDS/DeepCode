"""Pin the AI Stupid Level provider as a mirror of the Opper provider.

AI Stupid Level's Smart Router is an OpenAI-compatible gateway wired on the
same generic ``openai_compat`` path as OpenRouter, Requesty and Opper. These
tests assert the shared gateway wiring, pin the base URL, env var and key
prefix, and lock the two places it differs from Opper: its ids are bare, so a
``provider/`` prefix is stripped the way Forge's is, and the router asks
Claude to cache a conversation itself, so DeepCode sends no cache-control
markers.
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
from core.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402

ASL = find_by_name("aistupidlevel")
OPPER = find_by_name("opper")
FORGE = find_by_name("forge")


def _request(model: str, messages: list[dict]) -> dict:
    provider = OpenAICompatProvider(api_key="aism_test", spec=ASL)
    return provider._build_kwargs(
        messages,
        tools=None,
        model=model,
        max_tokens=512,
        temperature=0.2,
        reasoning_effort=None,
        tool_choice=None,
    )


# ---- registry --------------------------------------------------------------


def test_aistupidlevel_is_registered() -> None:
    assert ASL is not None
    assert ASL.name == "aistupidlevel"
    assert ASL.display_name == "AI Stupid Level"
    assert hasattr(ProvidersConfig(), "aistupidlevel")


def test_aistupidlevel_mirrors_the_generic_gateway_wiring() -> None:
    assert ASL is not None and OPPER is not None
    assert ASL.backend == OPPER.backend == "openai_compat"
    assert ASL.is_gateway is True
    assert ASL.endpoint_class == "gateway"
    assert ASL.is_local is False
    assert ASL.is_oauth is False


def test_aistupidlevel_provider_specific_endpoint() -> None:
    assert ASL is not None
    assert ASL.default_api_base == "https://aistupidlevel.info/v1"
    assert ASL.env_key == "AISTUPIDLEVEL_API_KEY"
    assert ASL.detect_by_base_keyword == "aistupidlevel.info"


def test_aistupidlevel_keys_carry_their_own_prefix() -> None:
    # Router keys start with ``aism_``, not OpenRouter's ``sk-or-``.
    assert ASL is not None
    assert ASL.detect_by_key_prefix == "aism_"


# ---- request shape ---------------------------------------------------------


def test_aistupidlevel_strips_a_provider_prefix() -> None:
    """Like Forge, the router resolves bare ids only.

    ``auto-coding`` (a routing mode) and ``claude-opus-4-7`` (a pinned model)
    are ids it serves; it answers ``aistupidlevel/auto-coding`` with
    ``model_not_found``.
    """
    assert ASL is not None and FORGE is not None
    assert ASL.strip_model_prefix is FORGE.strip_model_prefix is True
    hello = [{"role": "user", "content": "Hello"}]
    assert _request("aistupidlevel/auto-coding", hello)["model"] == "auto-coding"
    assert _request("auto-coding", hello)["model"] == "auto-coding"
    assert _request("claude-opus-4-7", hello)["model"] == "claude-opus-4-7"


def test_aistupidlevel_leaves_claude_caching_to_the_router() -> None:
    """A pinned Claude id gets no cache-control markers from DeepCode.

    Unlike Opper, the router itself asks Claude to cache every conversation
    that continues, so the markers would add nothing.
    """
    assert ASL is not None
    assert ASL.supports_prompt_caching is False
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix the failing test."},
        {"role": "assistant", "content": "Reading the test first."},
        {"role": "user", "content": "Go on."},
    ]
    sent = _request("claude-opus-4-7", messages)["messages"]
    assert [m["content"] for m in sent] == [m["content"] for m in messages]
