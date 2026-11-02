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
  and ``evidence_ledger_threshold=None`` disables the layer entirely;
- reminders are not enough on their own, so a run whose calls keep bringing
  nothing new is warned once and then stopped (``no_progress``): results it had
  already seen, or calls it had already made. New input starts the count over.
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
    REPEATED_CALL_STOP,
    REPEATED_CALL_WARNING,
    STALE_RESULT_STOP,
    STALE_RESULT_WARNING,
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


# --- the hard stop ---------------------------------------------------------


def _alternate(ledger: EvidenceLedger, calls: int) -> list[str | None]:
    """A, B, A, B, ... each with a constant result; the warnings it earned."""
    warnings = []
    for n in range(calls):
        ledger.observe("probe", {"side": n % 2}, "nothing")
        warnings.append(ledger.take_warning())
    return warnings


def test_seen_results_warn_once_then_stop_the_run() -> None:
    ledger = EvidenceLedger()
    # The first A and B are new; every later result was seen before.
    warnings = _alternate(ledger, STALE_RESULT_WARNING + 2)
    assert [w is not None for w in warnings].count(True) == 1
    assert warnings[-1] is not None
    assert "already seen" in warnings[-1]
    assert ledger.stalled is None

    _alternate(ledger, STALE_RESULT_STOP - STALE_RESULT_WARNING - 1)
    assert ledger.stalled is None
    _alternate(ledger, 1)
    assert ledger.stalled is not None
    assert f"last {STALE_RESULT_STOP} tool calls" in ledger.stalled


def test_a_new_result_or_a_new_call_starts_the_count_over() -> None:
    ledger = EvidenceLedger()
    _alternate(ledger, STALE_RESULT_STOP + 1)
    ledger.observe("probe", {"side": 0}, "something new")
    assert ledger.stalled is None

    _alternate(ledger, STALE_RESULT_STOP + 1)
    ledger.observe("probe", {"side": "fresh"}, "nothing")
    assert ledger.stalled is None


def test_repeated_calls_with_noisy_results_stop_later() -> None:
    """Re-running the same tests, where only the timing differs each time."""
    ledger = EvidenceLedger()
    warned = []
    for n in range(REPEATED_CALL_STOP):
        call = "pytest" if n % 2 else "cat test_calc.py"
        result = f"1 failed in 0.{n:02d}s" if n % 2 else "the same test file"
        ledger.observe("bash", {"command": call}, result)
        warned.append(ledger.take_warning())
        assert ledger.stalled is None
    # The file read repeats evidence but the test run never does, so only the
    # repeated-call streak grows (both calls were first made at n = 0 and 1).
    hits = [n for n, w in enumerate(warned) if w is not None]
    assert hits == [REPEATED_CALL_WARNING + 1]
    assert "already made" in warned[hits[0]]
    assert ledger.stalled is None
    ledger.observe("bash", {"command": "pytest"}, "1 failed in 9.99s")
    ledger.observe("bash", {"command": "cat test_calc.py"}, "the same test file")
    assert ledger.stalled is not None
    assert "already made" in ledger.stalled


def test_waiting_on_other_work_is_not_a_stall() -> None:
    """``wait_agent`` returns the same "still running" text by design."""
    ledger = EvidenceLedger()
    for _ in range(STALE_RESULT_STOP * 3):
        ledger.observe("wait_agent", {}, "still running", waits=True)
    assert ledger.stalled is None
    assert ledger.take_warning() is None
    # Waiting neither hides nor breaks a real loop around it.
    for n in range(STALE_RESULT_STOP + 2):
        ledger.observe("probe", {"side": n % 2}, "nothing")
        ledger.observe("wait_agent", {}, "still running", waits=True)
        ledger.take_warning()
    assert ledger.stalled is not None


def test_after_a_summary_reading_again_is_not_a_stall() -> None:
    ledger = EvidenceLedger()
    for n in range(STALE_RESULT_WARNING + 2):
        ledger.observe("read", {"file": n % 5}, f"contents {n % 5}")
    ledger.take_warning()
    streak_before = ledger._stale_results
    ledger.out_of_view()
    # Re-reading the five files once more is neither progress nor a stall.
    for n in range(5):
        ledger.observe("read", {"file": n}, f"contents {n}")
    assert ledger._stale_results == streak_before
    # A second read is back in view, so it counts again.
    ledger.observe("read", {"file": 0}, "contents 0")
    assert ledger._stale_results == streak_before + 1


def test_frequent_summaries_cannot_hide_a_loop() -> None:
    """Context full of a loop: a summary every few calls must not reset it."""
    ledger = EvidenceLedger()
    calls = 0
    while ledger.stalled is None and calls < 200:
        ledger.observe("probe", {"side": calls % 2}, "nothing")
        ledger.take_warning()
        calls += 1
        if calls % 3 == 0:
            ledger.out_of_view()
    assert ledger.stalled is not None
    assert calls < 60


def test_the_model_reads_the_warning_before_the_stop() -> None:
    """A batch of parallel calls can cross the warning and the stop at once."""
    ledger = EvidenceLedger()
    _alternate(ledger, 2)
    for n in range(STALE_RESULT_STOP + 2):  # one batch
        ledger.observe("probe", {"side": n % 2}, "nothing")
    assert ledger.take_warning() is not None
    assert ledger.stalled is None  # not before the model saw the warning
    ledger.observe("probe", {"side": 0}, "nothing")  # its next step
    assert ledger.stalled is not None


def test_the_stop_can_be_switched_off_and_reset() -> None:
    quiet = EvidenceLedger(stop=False)
    assert not any(_alternate(quiet, STALE_RESULT_STOP * 2))
    assert quiet.stalled is None

    ledger = EvidenceLedger()
    _alternate(ledger, STALE_RESULT_STOP + 2)
    assert ledger.stalled is not None
    ledger.reset_streaks()
    assert ledger.stalled is None
    assert ledger.take_warning() is None


class _StubbornProvider:
    """Alternates two calls forever, whatever it is told."""

    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{self.calls}",
                    name="probe",
                    arguments={"pattern": "left" if self.calls % 2 else "right"},
                )
            ],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 90, "completion_tokens": 10},
        )


def test_runner_stops_a_run_that_ignores_every_reminder() -> None:
    provider = _StubbornProvider()
    result = asyncio.run(
        AgentRunner(provider).run(_spec(provider, max_iterations=None))
    )

    assert result.stop_reason == "no_progress"
    # Two new results, then STALE_RESULT_STOP results already seen.
    assert provider.calls == STALE_RESULT_STOP + 2
    assert "not making progress" in result.final_content
    warnings = [
        m
        for m in result.messages
        if m.get("role") == "user" and "Warning:" in str(m.get("content"))
    ]
    assert len(warnings) == 1


def test_runner_starts_over_when_the_user_steers() -> None:
    provider = _StubbornProvider()
    steered = []

    async def steer_once() -> list[dict[str, Any]]:
        if provider.calls == STALE_RESULT_STOP + 2 and not steered:
            steered.append(True)
            return [{"role": "user", "content": "try something else"}]
        return []

    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, max_iterations=None, injection_callback=steer_once)
        )
    )

    assert steered
    assert result.stop_reason == "no_progress"
    # The steer arrived just as the run was due to stop, so the count started
    # over and the run went on until the model stalled a second time.
    assert provider.calls == 2 * STALE_RESULT_STOP + 2


def test_runner_no_progress_stop_can_be_disabled() -> None:
    provider = _StubbornProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, no_progress_stop=False, max_iterations=30)
        )
    )

    assert result.stop_reason == "max_iterations"
    assert provider.calls == 30


def test_runner_stops_when_the_token_allowance_is_spent() -> None:
    provider = _StubbornProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, max_iterations=None, token_allowance=lambda: 250)
        )
    )

    # 100 tokens a call: 300 >= 250 after the third. A fourth request wraps
    # up; this model only ever calls tools, so the fixed note stands in.
    assert result.stop_reason == "token_budget"
    assert provider.calls == 4
    assert "budget (tokens or time)" in result.final_content


class _WrapUpProvider(_StubbornProvider):
    """Calls tools while it may; answers in words when it may not."""

    def __init__(self) -> None:
        super().__init__()
        self.wrap_up_request: dict[str, Any] | None = None

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        if not kwargs.get("tools"):
            self.calls += 1
            self.wrap_up_request = kwargs
            return LLMResponse(
                content="Fixed the parser; the CLI flag is still to do.",
                finish_reason="stop",
            )
        return await super().chat_with_retry(**kwargs)


def test_a_spent_budget_gets_one_answer_without_tools() -> None:
    provider = _WrapUpProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, max_iterations=None, token_allowance=lambda: 250)
        )
    )

    assert result.stop_reason == "token_budget"
    assert result.final_content == "Fixed the parser; the CLI flag is still to do."
    assert provider.wrap_up_request is not None
    asked = provider.wrap_up_request["messages"][-1]
    assert asked["role"] == "user"
    assert "is used up" in asked["content"]
    assert "never state a result you did not observe" in asked["content"]
    # The wrap-up is spent too: the run's usage counts it.
    assert provider.calls == 4


def test_runner_ignores_an_allowance_it_cannot_read() -> None:
    provider = _StubbornProvider()

    def broken() -> int:
        raise RuntimeError("store unavailable")

    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, max_iterations=5, token_allowance=broken)
        )
    )

    assert result.stop_reason == "max_iterations"
    assert provider.calls == 5


class _Control:
    def __init__(self, running: int) -> None:
        self.running = running

    def active_count(self) -> int:
        return self.running


def test_waiting_tools_wait_only_while_sub_agents_run() -> None:
    from core.harness.tools.spawn_agent import ListAgentsTool, WaitAgentTool

    for tool_class in (WaitAgentTool, ListAgentsTool):
        assert tool_class(_Control(running=1)).waits is True
        # Nothing to wait for: a repeated answer is a loop like any other.
        assert tool_class(_Control(running=0)).waits is False


def test_a_budget_spent_before_the_run_gets_no_wrap_up() -> None:
    provider = _WrapUpProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, max_iterations=None, token_allowance=lambda: 0)
        )
    )

    assert result.stop_reason == "token_budget"
    assert provider.calls == 0
    assert "budget (tokens or time)" in result.final_content
