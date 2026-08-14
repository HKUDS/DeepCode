"""Mid-turn model-visible messages reach the host sink (``context_note_sink``).

The dsh session-log rule under test — model-visible means logged: every
message the runner itself adds to the PERSISTED model history (sub-agent
results and Goal updates drained from the mailbox, repeat-call reminders,
length-recovery prompts, stop-hook continuations) must be reported to the
host so the canonical Session can record them. Steering is NOT reported
here: the service that accepted it already persisted it, and a second copy
would duplicate history on replay.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.injections import (
    GoalObjectiveUpdated,
    SubagentMessage,
    UserSteer,
)
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest


@tool_parameters(
    {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }
)
class _ProbeTool(Tool):
    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "Probe for something."

    async def execute(self, **kwargs: Any) -> Any:
        return "nothing found"


class _RepeatingProvider:
    """Repeats one identical tool call, then stops after the reminder."""

    def __init__(self) -> None:
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        messages = kwargs.get("messages") or []
        if any(
            "repeating the exact same tool call" in str(m.get("content"))
            for m in messages
        ):
            return LLMResponse(content="done", finish_reason="stop")
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


def _spec(provider: Any, **overrides: Any) -> AgentRunSpec:
    registry = ToolRegistry()
    registry.register(_ProbeTool())
    defaults: dict[str, Any] = {
        "initial_messages": [{"role": "user", "content": "go"}],
        "tools": registry,
        "model": "fake-model",
        "max_iterations": 10,
        "max_tool_result_chars": 10_000,
    }
    defaults.update(overrides)
    return AgentRunSpec(**defaults)


def test_repeat_reminder_is_reported_to_the_sink() -> None:
    notes: list[tuple[str, str]] = []
    provider = _RepeatingProvider()
    result = asyncio.run(
        AgentRunner(provider).run(
            _spec(provider, context_note_sink=lambda c, s: notes.append((s, c)))
        )
    )
    assert result.final_content == "done"
    assert len(notes) == 1
    source, content = notes[0]
    assert source == "repeat_guard"
    assert "repeating the exact same tool call" in content


def test_subagent_injections_are_reported_but_steering_is_not() -> None:
    """One drain delivering a steer and a sub-agent result notes ONLY the
    sub-agent result — the steer was persisted by the service that took it."""

    notes: list[tuple[str, str]] = []
    delivered = False

    async def injection_callback(**_kwargs: Any) -> list[Any]:
        nonlocal delivered
        if delivered:
            return []
        delivered = True
        return [
            UserSteer(
                message_id="steer-1",
                target_turn_id="turn-1",
                text="please also check the docs",
            ),
            SubagentMessage(
                message_id="sub-1",
                target_turn_id="turn-1",
                agent_id="worker",
                payload="RESULT: subtask finished",
            ),
        ]

    class _OneShotProvider:
        def get_default_model(self) -> str:
            return "fake-model"

        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="done", finish_reason="stop")

    result = asyncio.run(
        AgentRunner(_OneShotProvider()).run(
            _spec(
                _OneShotProvider(),
                injection_callback=injection_callback,
                context_note_sink=lambda c, s: notes.append((s, c)),
            )
        )
    )
    assert result.final_content == "done"
    assert [source for source, _ in notes] == ["subagent"]
    assert "RESULT: subtask finished" in notes[0][1]


def test_goal_update_injection_is_reported() -> None:
    """The Goal ledger stores the objective, but the exact prompt text the
    model read exists only in this run's memory — it must be noted."""

    notes: list[tuple[str, str]] = []
    delivered = False

    async def injection_callback(**_kwargs: Any) -> list[Any]:
        nonlocal delivered
        if delivered:
            return []
        delivered = True
        return [
            GoalObjectiveUpdated(
                message_id="goal-1",
                target_turn_id="turn-1",
                goal_id="g-42",
                objective="ship the refactor",
            )
        ]

    class _OneShotProvider:
        def get_default_model(self) -> str:
            return "fake-model"

        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="done", finish_reason="stop")

    asyncio.run(
        AgentRunner(_OneShotProvider()).run(
            _spec(
                _OneShotProvider(),
                injection_callback=injection_callback,
                context_note_sink=lambda c, s: notes.append((s, c)),
            )
        )
    )
    assert [source for source, _ in notes] == ["goal_update"]
    assert "ship the refactor" in notes[0][1]


def test_stop_hook_continuation_is_reported() -> None:
    notes: list[tuple[str, str]] = []
    fired = False

    async def stop_hook(_active: bool):
        nonlocal fired
        if fired:
            return None
        fired = True

        class _Outcome:
            block = True
            block_reason = "also run the tests before finishing"

        return _Outcome()

    class _Provider:
        def get_default_model(self) -> str:
            return "fake-model"

        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content="done", finish_reason="stop")

    result = asyncio.run(
        AgentRunner(_Provider()).run(
            _spec(
                _Provider(),
                stop_hook=stop_hook,
                context_note_sink=lambda c, s: notes.append((s, c)),
            )
        )
    )
    assert result.final_content == "done"
    assert ("stop_hook", "also run the tests before finishing") in notes


def test_length_recovery_prompt_is_reported() -> None:
    notes: list[tuple[str, str]] = []

    class _TruncatingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_default_model(self) -> str:
            return "fake-model"

        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="partial answ", finish_reason="length")
            return LLMResponse(content="er, completed", finish_reason="stop")

    result = asyncio.run(
        AgentRunner(_TruncatingProvider()).run(
            _spec(
                _TruncatingProvider(),
                context_note_sink=lambda c, s: notes.append((s, c)),
            )
        )
    )
    assert result.error is None
    assert [source for source, _ in notes] == ["length_recovery"]


def test_sink_failure_never_aborts_the_run() -> None:
    def broken_sink(_content: str, _source: str) -> None:
        raise OSError("disk full")

    provider = _RepeatingProvider()
    result = asyncio.run(
        AgentRunner(provider).run(_spec(provider, context_note_sink=broken_sink))
    )
    # The reminder still reached the model (the provider stops on seeing it),
    # and the run settled normally despite the sink failing every time.
    assert result.final_content == "done"
    assert result.error is None
