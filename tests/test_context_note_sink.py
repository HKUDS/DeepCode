"""Mid-turn model-visible messages reach the host sink (``context_note_sink``).

The dsh session-log rule under test — model-visible means logged: messages
the runner itself adds to model history mid-turn (sub-agent results drained
from the mailbox, repeat-call reminders) must be reported to the host so the
canonical Session can record them. Steering and Goal updates are NOT
reported here: the services that accepted them already persisted them, and a
second copy would duplicate history on replay.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.injections import SubagentMessage, UserSteer
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
