"""Advisory repeat-call reminders (``core.agent_runtime.repeat_guard``).

The contract under test, borrowed from dsh's repeat-tool-reminder guard:

- identical consecutive calls escalate through the configured thresholds —
  gentle first, then detailed with the canonical arguments quoted;
- nothing is ever delayed or blocked, and a changed call resets the chain;
- argument-order differences do not defeat detection (canonicalization), and
  oversized arguments cannot evade it (the cap bounds only the quote);
- inside the runner, the reminder lands as a user message after the tool
  results of the batch that crossed the threshold.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.repeat_guard import RepeatCallTracker
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest


def test_escalates_gentle_then_detailed_at_exact_thresholds() -> None:
    tracker = RepeatCallTracker((3, 5))
    hits = [tracker.observe("grep", {"pattern": "x"}) for _ in range(6)]

    assert hits[0] is None and hits[1] is None
    assert hits[2] is not None and "different approach" in hits[2]
    assert hits[3] is None
    assert hits[4] is not None and "consecutive_calls: 5" in hits[4]
    # Past the last threshold the chain keeps counting but stays quiet.
    assert hits[5] is None


def test_changed_arguments_reset_the_chain() -> None:
    tracker = RepeatCallTracker((3,))
    assert tracker.observe("grep", {"pattern": "a"}) is None
    assert tracker.observe("grep", {"pattern": "a"}) is None
    # A different call breaks the run — no reminder on the third overall call.
    assert tracker.observe("grep", {"pattern": "b"}) is None
    assert tracker.observe("grep", {"pattern": "b"}) is None
    assert tracker.observe("grep", {"pattern": "b"}) is not None


def test_argument_order_does_not_defeat_detection() -> None:
    tracker = RepeatCallTracker((2,))
    assert tracker.observe("t", {"a": 1, "b": {"c": 2, "d": 3}}) is None
    assert tracker.observe("t", {"b": {"d": 3, "c": 2}, "a": 1}) is not None


def test_detection_key_is_never_truncated_but_the_quote_is() -> None:
    # (2, 3): the first hit is the gentle tier; the DETAILED tier at 3 is the
    # one that quotes (and caps) the canonical arguments.
    tracker = RepeatCallTracker((2, 3), arguments_preview_chars=40)
    big = {"payload": "x" * 500}
    assert tracker.observe("t", big) is None
    assert tracker.observe("t", big) is not None  # gentle, no quote
    reminder = tracker.observe("t", big)
    assert reminder is not None
    assert "more chars" in reminder
    # A call that differs only past the preview cap still resets the chain:
    # detection used the full canonical string.
    other = {"payload": "x" * 499 + "y"}
    assert tracker.observe("t", other) is None


@pytest.mark.parametrize("thresholds", [(), (1,), (3, 3), (2.5,), (True, 3)])
def test_invalid_thresholds_fail_loud(thresholds: tuple) -> None:
    with pytest.raises(ValueError):
        RepeatCallTracker(thresholds)  # type: ignore[arg-type]


@tool_parameters(
    {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }
)
class _NoisyTool(Tool):
    """Always returns the same unhelpful answer — loop bait."""

    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "Probe for something."

    async def execute(self, **kwargs: Any) -> Any:
        return "nothing found"


class _LoopingProvider:
    """Repeats the same tool call until the reminder appears, then stops.

    This is the behavior the guard exists for: the model only changes course
    once it is told what it is repeating.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.saw_reminder_at: int | None = None

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        messages = kwargs.get("messages") or []
        if any(
            m.get("role") == "user"
            and "repeating the exact same tool call" in str(m.get("content"))
            for m in messages
        ):
            self.saw_reminder_at = self.calls
            return LLMResponse(content="changing course", finish_reason="stop")
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{self.calls}",
                    name="probe",
                    arguments={"pattern": "same"},
                )
            ],
            finish_reason="tool_calls",
        )


def _spec(provider: _LoopingProvider, **overrides: Any) -> AgentRunSpec:
    registry = ToolRegistry()
    registry.register(_NoisyTool())
    defaults: dict[str, Any] = {
        "initial_messages": [{"role": "user", "content": "find it"}],
        "tools": registry,
        "model": provider.get_default_model(),
        "max_iterations": 20,
        "max_tool_result_chars": 10_000,
    }
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


def test_runner_injects_reminder_and_the_model_recovers() -> None:
    provider = _LoopingProvider()
    result = asyncio.run(AgentRunner(provider).run(_spec(provider)))

    assert result.final_content == "changing course"
    # Default thresholds start at 3: the reminder followed the third identical
    # call, so the model saw it on its fourth sample.
    assert provider.saw_reminder_at == 4
    reminder_messages = [
        m
        for m in result.messages
        if m.get("role") == "user"
        and "repeating the exact same tool call" in str(m.get("content"))
    ]
    assert len(reminder_messages) == 1
    # The reminder rides AFTER the tool result it comments on.
    reminder_index = result.messages.index(reminder_messages[0])
    assert result.messages[reminder_index - 1].get("role") == "tool"


def test_runner_guard_can_be_disabled() -> None:
    provider = _LoopingProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, repeat_call_thresholds=None, max_iterations=6)
        )
    )
    # No reminder ever appears; the run ends by the hard iteration limit —
    # which is exactly the fallback role hard stops keep.
    assert provider.saw_reminder_at is None
    assert result.stop_reason == "max_iterations"
