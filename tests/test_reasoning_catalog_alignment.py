"""Reasoning capabilities resolve through the model catalog, not a name chain.

The previous resolver was a chain of ``if "kimi-k3" in model`` checks living
apart from the catalog. It rotted exactly as its own docstring predicted: it
recognised ``claude-sonnet-4-6`` while the catalog had moved on to
``claude-sonnet-5``, so the current Anthropic and DeepSeek line-ups advertised
no thinking controls at all and the Desktop effort picker rendered empty.

These tests pin the properties that failure violated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.catalog import _SEED, resolve_model_info  # noqa: E402
from core.providers.reasoning import infer_reasoning_capabilities  # noqa: E402


@pytest.mark.parametrize(
    "model_id",
    [
        "deepseek-reasoner",
        "deepseek-r1",
        "deepseek-chat",
        "deepseek/deepseek-v3",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "gpt-5.4",
        "o3",
        "kimi-k3",
        "qwen3-max",
        "grok-4",
    ],
)
def test_shipping_models_advertise_reasoning(model_id: str) -> None:
    """Every reasoning-capable model DeepCode seeds must say so.

    Each id here returned ``None`` before the resolver moved into the catalog,
    which is what left the effort picker empty.
    """

    assert infer_reasoning_capabilities(model_id) is not None


@pytest.mark.parametrize(
    "model_id",
    ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "kimi-k2"],
)
def test_non_reasoning_models_stay_silent(model_id: str) -> None:
    """The fix must not hand thinking controls to models without them."""

    assert infer_reasoning_capabilities(model_id) is None


@pytest.mark.parametrize(
    ("unseen", "family_member"),
    [
        ("deepseek-r2", "deepseek-r1"),
        ("gpt-5.9", "gpt-5"),
        ("kimi-k3-turbo", "kimi-k3"),
    ],
)
def test_unseen_models_inherit_their_family(unseen: str, family_member: str) -> None:
    """A model the seed has never seen resolves through its family rule.

    This is the property a name chain cannot offer: releasing ``deepseek-r2``
    should not require editing code for it to keep its thinking controls.
    """

    inherited = infer_reasoning_capabilities(unseen)
    assert inherited is not None
    assert inherited == infer_reasoning_capabilities(family_member)


def test_family_fallback_does_not_claim_summarised_thinking() -> None:
    """A dated Claude 4 id predates summarisation and must not claim it.

    ``claude-sonnet-4-20250514`` streams raw thinking blocks. Routing those
    through the summary channel would surface private trace text as a summary,
    so the family fallback deliberately advertises less than its seed row.
    """

    dated = infer_reasoning_capabilities("claude-sonnet-4-20250514")
    seeded = infer_reasoning_capabilities("claude-sonnet-5")

    assert dated is not None and seeded is not None
    assert dated.supported_efforts == seeded.supported_efforts
    assert dated.supports_summary is False
    assert seeded.supports_summary is True


def test_remote_parameters_still_win_for_unseeded_models() -> None:
    """A model absent from the catalog is believed when its catalog says so.

    No named levels come with that signal, so the surface stays Auto/Off
    rather than inventing effort names the provider never published.
    """

    capabilities = infer_reasoning_capabilities(
        "some-vendor/brand-new-thinker",
        supported_parameters=("tools", "reasoning_effort"),
    )
    assert capabilities is not None
    assert capabilities.supported_efforts == ()


def test_capabilities_travel_with_the_rest_of_the_model_row() -> None:
    """One id, one lookup: limits and thinking come from the same cascade."""

    info = resolve_model_info("deepseek-r1")
    assert info.context_window == _SEED["deepseek-r1"].context_window
    assert info.reasoning is infer_reasoning_capabilities("deepseek-r1")


@pytest.mark.parametrize(
    ("model_id", "context_window"),
    [
        # docs.z.ai/guides/llm/glm-4.6 — 200K in, 128K out.
        ("glm-4.6", 204_800),
        ("zai/glm-4.6", 204_800),
        ("glm-4.7", 204_800),
        # platform.minimax.io — the M2 line shares one window, M3 is the
        # long-context tier, and the published figure is input+output.
        ("MiniMax-M2", 204_800),
        ("MiniMax-M2.5", 204_800),
        ("MiniMax-M3", 1_000_000),
    ],
)
def test_zhipu_and_minimax_resolve_off_the_default(
    model_id: str, context_window: int
) -> None:
    """Both lines used to land on the 128K default with no thinking at all.

    That is the failure mode the default is designed to avoid: a 200K model
    trimmed to 128K, and a thinking-capable one advertising nothing.
    """

    info = resolve_model_info(model_id)
    assert info.source != "default"
    assert info.context_window == context_window
    assert info.reasoning is not None


def test_minimax_long_context_tier_beats_the_general_prefix() -> None:
    """Rule order matters: ``minimax-m3`` must win over ``minimax``."""

    assert resolve_model_info("minimax-m3-preview").context_window == 1_000_000
    assert resolve_model_info("minimax-m2.1").context_window == 204_800
