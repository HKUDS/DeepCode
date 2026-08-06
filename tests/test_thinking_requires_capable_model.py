"""A thinking body needs both a capable model and an endpoint that spells it.

``ProviderSpec.thinking_style`` describes the *endpoint's dialect* — how this
API writes a thinking toggle. It says nothing about whether the model behind
it has one. Applying it on the provider alone meant a model with no thinking
mode still received ``thinking: {"type": "enabled"}`` whenever it happened to
be routed through DeepSeek- or Zhipu-compatible endpoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.model_compat import (  # noqa: E402
    model_supports_thinking,
    resolve_model_compat,
)
from core.providers.registry import find_by_name  # noqa: E402

# Every provider that declares a thinking dialect.
THINKING_ENDPOINTS = ["deepseek", "zhipu", "dashscope"]


@pytest.mark.parametrize("provider", THINKING_ENDPOINTS)
@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "llama3", "kimi-k2"])
def test_models_without_a_thinking_mode_are_never_sent_one(
    provider: str, model: str
) -> None:
    compat = resolve_model_compat(
        model_name=model,
        spec=find_by_name(provider),
        reasoning_effort="high",
    )
    assert compat.thinking_extra_body is None
    # The echo exists to pair with a thinking turn; without one it is noise.
    assert compat.inject_empty_reasoning_content is False


@pytest.mark.parametrize("provider", THINKING_ENDPOINTS)
@pytest.mark.parametrize("model", ["deepseek-chat", "glm-4.6", "qwen3-max"])
def test_capable_models_still_get_the_endpoint_dialect(
    provider: str, model: str
) -> None:
    """The gate narrows by model; it must not disable the feature."""

    compat = resolve_model_compat(
        model_name=model,
        spec=find_by_name(provider),
        reasoning_effort="high",
    )
    assert compat.thinking_extra_body is not None


def test_an_endpoint_without_a_dialect_sends_nothing() -> None:
    """Capability alone is not enough — the endpoint must spell it too."""

    compat = resolve_model_compat(
        model_name="deepseek-chat",
        spec=find_by_name("openai"),
        reasoning_effort="high",
    )
    assert compat.thinking_extra_body is None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("deepseek-chat", True),
        ("deepseek-r2", True),  # unseen; inherits the deepseek-r family row
        ("glm-4.7", True),  # unseen; inherits the glm family row
        ("claude-sonnet-5", True),
        ("gpt-4o", False),
        ("llama3", False),
    ],
)
def test_capability_follows_the_catalog_not_a_token_list(
    model: str, expected: bool
) -> None:
    assert model_supports_thinking(model) is expected
