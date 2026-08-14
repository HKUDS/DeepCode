"""Zhipu declares the thinking body shape its API actually expects.

GLM enables reasoning with ``thinking: {"type": "enabled"}``
(docs.z.ai/guides/llm/glm-4.6) — the same body DeepSeek takes, and the shape
``_THINKING_STYLE_BUILDERS["thinking_type"]`` already builds. The provider
spec simply never declared it, so every GLM request went out with reasoning
silently omitted no matter what effort the user picked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.model_compat import resolve_model_compat  # noqa: E402
from core.providers.registry import find_by_name  # noqa: E402


@pytest.mark.parametrize(
    ("effort", "expected"),
    [("high", "enabled"), ("low", "enabled"), ("none", "disabled")],
)
def test_glm_requests_carry_a_thinking_body(effort: str, expected: str) -> None:
    compat = resolve_model_compat(
        model_name="glm-4.6",
        spec=find_by_name("zhipu"),
        reasoning_effort=effort,
    )
    assert compat.thinking_extra_body == {"thinking": {"type": expected}}


@pytest.mark.parametrize("effort", [None, "auto"])
def test_auto_leaves_the_choice_to_the_model(effort: str | None) -> None:
    """``auto`` must not pin the switch either way — that is its whole point."""

    compat = resolve_model_compat(
        model_name="glm-4.6",
        spec=find_by_name("zhipu"),
        reasoning_effort=effort,
    )
    assert compat.thinking_extra_body is None


def test_zhipu_matches_the_deepseek_wire_shape() -> None:
    """Both vendors take the same body; the specs should agree on it."""

    assert find_by_name("zhipu").thinking_style == "thinking_type"
    assert find_by_name("deepseek").thinking_style == "thinking_type"
