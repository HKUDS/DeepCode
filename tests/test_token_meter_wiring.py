"""The anchored meter has to reach the pressure gate, across Turns.

Unit tests prove the meter prices correctly in isolation; this proves the
wiring. A provider that reports a prompt size far above what the text would
estimate must make the NEXT Turn compact. The Turn boundary is the point of
the test: `AgentRunSpec` is rebuilt every Turn, so a meter owned by the spec
would lose its anchor exactly where an accurate one matters most — the first
request of a new Turn over a long history.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.registry import ToolRegistry
from core.events import AgentSession, UserInput
from core.providers.base import LLMResponse


class _ReportingProvider:
    """Answers briefly while reporting whatever prompt size it is told to."""

    def __init__(self, reported: int) -> None:
        self.reported = reported
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.requests.append(list(kwargs.get("messages") or []))
        return LLMResponse(
            content=f"answer {self.calls}",
            finish_reason="stop",
            usage={"prompt_tokens": self.reported, "completion_tokens": 4},
        )


async def _turns(reported: int, count: int = 3) -> _ReportingProvider:
    provider = _ReportingProvider(reported)
    session = AgentSession(
        provider=provider,
        tools=ToolRegistry(),
        model="fake-model",
        context_window_tokens=10_000,
    )
    # The pressure gate also requires a few real messages before a summary is
    # worth a round-trip, so this drives enough Turns to clear that floor.
    for index in range(count):
        await session.submit(UserInput(text=f"question {index}"))
    return provider


@pytest.mark.asyncio
async def test_a_reported_prompt_size_reaches_the_next_turns_gate() -> None:
    """9,500 reported against a 10,000 window must trigger compaction."""
    provider = await _turns(9_500)
    # Each Turn answers in one call; any call beyond that is the summarizer,
    # which only runs because the gate believed the provider over the
    # estimator — the estimator prices this conversation in the dozens.
    assert provider.calls > 3, (
        "the anchor did not survive the Turn boundary: the gate still priced "
        "the conversation with the estimator"
    )


@pytest.mark.asyncio
async def test_a_small_reported_prompt_size_leaves_the_turn_alone() -> None:
    provider = await _turns(10)
    assert provider.calls == 3, "nothing was under pressure; nothing should compact"
