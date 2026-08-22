"""A refused compaction is not paid for twice.

Under sustained context pressure the gate fires on every step. A history
with nothing older to replace — one long turn — produces the same summary
and the same convergence refusal every time. Each of those attempts is a
real model round-trip. This pins that the second attempt over an unchanged
history costs nothing, and that a changed history is tried again.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.runner import AgentRunner, AgentRunSpec  # noqa: E402
from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _UselessSummarizer:
    """Answers every summarize request with something longer than its input."""

    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="P" * 20_000, finish_reason="stop")


def _history(tail: str = "") -> list[dict[str, Any]]:
    """Six real turns — comfortably over the 3,000-token window below."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "sys"}]
    for index in range(6):
        messages.append(
            {
                "role": "user",
                "content": f"question {index}: explain this module's tradeoffs. " * 20,
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"answer {index}: it renders the event stream. " * 20,
            }
        )
    if tail:
        messages.append({"role": "user", "content": tail})
    return messages


def _spec() -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[],
        tools=ToolRegistry(),
        model="fake-model",
        max_iterations=1,
        max_tool_result_chars=1000,
        context_window_tokens=3_000,
        max_tokens=500,
    )


def test_an_unchanged_history_is_not_summarized_twice() -> None:
    provider = _UselessSummarizer()
    runner = AgentRunner(provider)
    spec = _spec()

    first = asyncio.run(runner._maybe_compact(spec, _history()))
    assert provider.calls == 1, "the first attempt should happen"
    assert len(first) == len(_history()), "and should be refused (no shrink)"

    asyncio.run(runner._maybe_compact(spec, _history()))
    assert provider.calls == 1, "the same history must not be summarized again"


def test_a_changed_history_is_tried_again() -> None:
    provider = _UselessSummarizer()
    runner = AgentRunner(provider)
    spec = _spec()

    asyncio.run(runner._maybe_compact(spec, _history()))
    assert provider.calls == 1

    asyncio.run(runner._maybe_compact(spec, _history(tail="something new happened")))
    assert provider.calls == 2, "new content deserves a fresh attempt"
