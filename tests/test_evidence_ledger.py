"""Advisory no-progress reminders (``core.agent_runtime.evidence_ledger``).

The contract under test:

- a call that keeps handing back the same result earns exactly one reminder at
  the threshold — consecutive or interleaved;
- the complementary case is the point: ``repeat_guard`` is structurally blind to
  A, B, A, B, ... stalls, where no call is ever repeated consecutively;
- new evidence (a different result for the same call) restarts that pair's
  count, and argument-order differences do not defeat detection;
- the reminder never quotes tool output, and the ledger's state stays bounded;
- results are a dynamic boundary: a tool may hand back structured content (the
  Goal tools return dicts) and counting must not raise on it;
- inside the runner the reminder lands as a user message after the tool results,
  calls ``repeat_guard`` already flagged are left to it (one reminder per call),
  and ``evidence_ledger_threshold=None`` disables the layer entirely.
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

from core.agent_runtime.evidence_ledger import (
    _MAX_TRACKED_CALLS,
    EvidenceLedger,
)
from core.agent_runtime.repeat_guard import RepeatCallTracker
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest


def test_repeats_the_same_evidence_once_at_the_threshold() -> None:
    ledger = EvidenceLedger(3)
    hits = [ledger.observe("grep", {"pattern": "x"}, "nothing found") for _ in range(4)]

    assert hits[0] is None and hits[1] is None
    assert hits[2] is not None and "same result 3 times" in hits[2]
    # One nudge per stalled call, not one per retry.
    assert hits[3] is None


def test_detects_the_interleaved_stall_repeat_guard_cannot_see() -> None:
    """A, B, A, B, A: never consecutive, never new evidence."""
    ledger = EvidenceLedger(3)
    tracker = RepeatCallTracker((3,))
    calls = [("a", "A"), ("b", "B"), ("a", "A"), ("b", "B"), ("a", "A")]

    ledger_hits = [ledger.observe(name, {"n": 1}, result) for name, result in calls]
    tracker_hits = [tracker.observe(name, {"n": 1}) for name, _ in calls]

    assert tracker_hits == [None] * 5  # the consecutive-only layer stays silent
    assert ledger_hits[:4] == [None] * 4
    assert ledger_hits[4] is not None and "`a`" in ledger_hits[4]


def test_new_evidence_restarts_the_count_for_that_result() -> None:
    ledger = EvidenceLedger(3)
    assert ledger.observe("probe", {"n": 1}, "alpha") is None
    assert ledger.observe("probe", {"n": 1}, "alpha") is None
    # Progress: a different answer for the same call is fresh evidence.
    assert ledger.observe("probe", {"n": 1}, "beta") is None
    assert ledger.observe("probe", {"n": 1}, "beta") is None
    assert ledger.observe("probe", {"n": 1}, "beta") is not None


def test_argument_order_does_not_defeat_detection() -> None:
    ledger = EvidenceLedger(2)
    assert ledger.observe("t", {"a": 1, "b": {"c": 2, "d": 3}}, "same") is None
    assert ledger.observe("t", {"b": {"d": 3, "c": 2}, "a": 1}, "same") is not None


def test_the_reminder_never_quotes_tool_output() -> None:
    ledger = EvidenceLedger(2)
    secret = "SECRET-FROM-THE-TOOL-OUTPUT"
    assert ledger.observe("read_file", {"path": "x"}, secret) is None
    reminder = ledger.observe("read_file", {"path": "x"}, secret)

    assert reminder is not None
    assert secret not in reminder
    assert "read_file" in reminder


def test_structured_results_are_evidence_too() -> None:
    """A tool result is a dynamic boundary: the Goal tools hand back dicts."""
    ledger = EvidenceLedger(2)
    first = {"goalId": "goal_1", "status": "in_progress"}
    same_in_another_order = {"status": "in_progress", "goalId": "goal_1"}

    assert ledger.observe("goal_probe", {"goalId": "goal_1"}, first) is None
    # Key order does not make it new evidence, and nothing raised on the way.
    assert (
        ledger.observe("goal_probe", {"goalId": "goal_1"}, same_in_another_order)
        is not None
    )


@pytest.mark.parametrize("threshold", [0, 1, -1, 2.5, True, "3"])
def test_invalid_thresholds_fail_loud(threshold: Any) -> None:
    with pytest.raises(ValueError):
        EvidenceLedger(threshold)  # type: ignore[arg-type]


def test_tracked_calls_stay_bounded_and_the_oldest_is_evicted() -> None:
    ledger = EvidenceLedger(3)
    for index in range(_MAX_TRACKED_CALLS + 1):
        ledger.observe("probe", {"n": index}, "same")

    # The oldest call fell out of the window, so its evidence started over and
    # the full threshold is needed again.
    assert ledger.observe("probe", {"n": 0}, "same") is None
    assert ledger.observe("probe", {"n": 0}, "same") is None
    assert ledger.observe("probe", {"n": 0}, "same") is not None
    # A retained call kept counting: one observation short of the threshold
    # before this pair is touched again, so it fires one call earlier. Bounded
    # memory must not cost accuracy on what is still in the window.
    assert ledger.observe("probe", {"n": _MAX_TRACKED_CALLS}, "same") is None
    assert ledger.observe("probe", {"n": _MAX_TRACKED_CALLS}, "same") is not None


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
        pattern = kwargs.get("pattern")
        return f"nothing found for {pattern}"


@tool_parameters(
    {
        "type": "object",
        "properties": {},
    }
)
class _StructuredTool(Tool):
    """Always returns the same dict — structured, not text, loop bait."""

    @property
    def name(self) -> str:
        return "goal_probe"

    @property
    def description(self) -> str:
        return "Read the current goal."

    async def execute(self, **kwargs: Any) -> Any:
        return {"goalId": "goal_1", "status": "in_progress"}


class _AlternatingProvider:
    """Interleaves two different calls, each returning a constant answer.

    This is the A, B, A, B, ... stall ``repeat_guard`` cannot catch: no call is
    ever repeated consecutively, so only result evidence reveals the loop.
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
            and "has now returned the same result" in str(m.get("content"))
            for m in messages
        ):
            self.saw_reminder_at = self.calls
            return LLMResponse(content="changing course", finish_reason="stop")
        pattern = "left" if self.calls % 2 else "right"
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{self.calls}",
                    name="probe",
                    arguments={"pattern": pattern},
                )
            ],
            finish_reason="tool_calls",
        )


class _RepeatingProvider:
    """Hammers one identical call until ``repeat_guard`` speaks.

    Both layers fire at three, so this is where overlap would show: the guard
    flags the third call, and the ledger must leave that call to it rather than
    stacking a second reminder into the same turn.
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


class _StructuredProvider:
    """Hammers one dict-returning call until the no-progress reminder lands."""

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
            and "has now returned the same result" in str(m.get("content"))
            for m in messages
        ):
            self.saw_reminder_at = self.calls
            return LLMResponse(content="changing course", finish_reason="stop")
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{self.calls}", name="goal_probe", arguments={}
                )
            ],
            finish_reason="tool_calls",
        )


def _spec(provider: Any, **overrides: Any) -> AgentRunSpec:
    registry = ToolRegistry()
    registry.register(_NoisyTool())
    registry.register(_StructuredTool())
    defaults: dict[str, Any] = {
        "initial_messages": [{"role": "user", "content": "find it"}],
        "tools": registry,
        "model": provider.get_default_model(),
        "max_iterations": 20,
        "max_tool_result_chars": 10_000,
    }
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


def test_runner_injects_a_no_progress_reminder_where_the_guard_is_blind() -> None:
    provider = _AlternatingProvider()
    result = asyncio.run(AgentRunner(provider).run(_spec(provider)))

    assert result.final_content == "changing course"
    # Threshold 3: the reminder followed the fifth call (A, B, A, B, A), and
    # the model saw it on its sixth sample.
    assert provider.saw_reminder_at == 6
    reminder_messages = [
        m
        for m in result.messages
        if m.get("role") == "user"
        and "has now returned the same result" in str(m.get("content"))
    ]
    assert len(reminder_messages) == 1
    assert "`probe`" in str(reminder_messages[0]["content"])
    # The reminder rides AFTER the tool result it comments on.
    reminder_index = result.messages.index(reminder_messages[0])
    assert result.messages[reminder_index - 1].get("role") == "tool"
    # Nothing was delayed, rewritten, or blocked: the guard is advisory.
    assert [m["role"] for m in result.messages].count("tool") == 5


def test_runner_lets_repeat_guard_own_consecutive_repeats() -> None:
    """Both layers fire at 3; the flagged call stays with ``repeat_guard``."""
    provider = _RepeatingProvider()
    result = asyncio.run(AgentRunner(provider).run(_spec(provider)))

    # The model read the guard's reminder on its fourth sample and stopped.
    assert provider.saw_reminder_at == 4
    injected = [m for m in result.messages if m.get("role") == "user"][1:]
    # Exactly one reminder. The ledger had already counted this call's evidence
    # twice, so without the hand-off the third call would push it to its own
    # threshold and this turn would carry two reminders for one stalled call.
    assert len(injected) == 1
    assert "repeating the exact same tool call" in str(injected[0]["content"])
    assert "has now returned the same result" not in str(injected[0]["content"])
    # Advisory throughout: the call still ran.
    assert [m["role"] for m in result.messages].count("tool") == 3


def test_runner_treats_a_structured_result_as_evidence() -> None:
    """Counting must survive a non-text result — the Goal tools return dicts.

    Raising here would be far worse than a missed reminder: the session turns an
    unexpected runner exception into an error event and the turn dies.
    """
    provider = _StructuredProvider()
    result = asyncio.run(
        AgentRunner(provider).run(_spec(provider, repeat_call_thresholds=None))
    )

    assert result.stop_reason == "completed"
    assert result.final_content == "changing course"
    assert provider.saw_reminder_at == 4
    assert [m["role"] for m in result.messages].count("tool") == 3


def test_runner_evidence_ledger_can_be_disabled() -> None:
    provider = _AlternatingProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, evidence_ledger_threshold=None, max_iterations=6)
        )
    )

    assert provider.saw_reminder_at is None
    assert not [
        m
        for m in result.messages
        if m.get("role") == "user" and "Reminder:" in str(m.get("content"))
    ]
    assert result.stop_reason == "max_iterations"
