"""Tests for the SQ/EQ AgentSession — the protocol's real engine.

Drives a full submit → event-stream cycle offline with a scripted provider
and a fake tool, proving the SQ/EQ protocol + kernel bridge work end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.base import Tool, tool_parameters  # noqa: E402
from core.agent_runtime.tools.registry import ToolRegistry  # noqa: E402
from core.events import (  # noqa: E402
    AgentMessage,
    AgentSession,
    Interrupt,
    Shutdown,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    UserInput,
)
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402


@tool_parameters(
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
)
class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo text."

    async def execute(self, **kwargs: Any) -> Any:
        return f"echo: {kwargs.get('text', '')}"


class ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[i]


def _tools() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _session(provider, **kw) -> AgentSession:
    return AgentSession(provider, _tools(), model="fake-model", **kw)


def _types(events):
    return [e.msg.type for e in events]


@pytest.mark.asyncio
async def test_plain_text_turn_emits_started_message_complete():
    provider = ScriptedProvider([LLMResponse(content="hello", finish_reason="stop")])
    session = _session(provider)

    await session.submit(UserInput(text="hi"))
    events = session.drain_events()

    assert _types(events) == ["turn_started", "agent_message", "task_complete"]
    assert isinstance(events[1].msg, AgentMessage)
    assert events[1].msg.text == "hello"
    complete = events[-1].msg
    assert isinstance(complete, TaskComplete)
    assert complete.stop_reason == "completed"


@pytest.mark.asyncio
async def test_tool_turn_emits_tool_started_and_completed():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    session = _session(provider)

    await session.submit(UserInput(text="use echo"))
    events = session.drain_events()

    kinds = _types(events)
    assert kinds[0] == "turn_started"
    assert "tool_started" in kinds and "tool_completed" in kinds
    started = [e.msg for e in events if isinstance(e.msg, ToolStarted)][0]
    completed = [e.msg for e in events if isinstance(e.msg, ToolCompleted)][0]
    assert started.name == "echo"
    assert completed.is_error is False
    assert kinds[-1] == "task_complete"


@pytest.mark.asyncio
async def test_denied_tool_marks_completed_as_error():
    provider = ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="echo", arguments={"text": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="ok", finish_reason="stop"),
        ]
    )

    def deny(_name, _args):
        return ("deny", "blocked")

    session = _session(provider, permission_checker=deny)
    await session.submit(UserInput(text="go"))
    events = session.drain_events()

    completed = [e.msg for e in events if isinstance(e.msg, ToolCompleted)][0]
    assert completed.is_error is True


@pytest.mark.asyncio
async def test_history_persists_across_turns():
    provider = ScriptedProvider(
        [
            LLMResponse(content="a", finish_reason="stop"),
            LLMResponse(content="b", finish_reason="stop"),
        ]
    )
    session = _session(provider)
    await session.submit(UserInput(text="first"))
    await session.submit(UserInput(text="second"))
    roles_contents = [(m["role"], m.get("content")) for m in session.history]
    # two user turns + two assistant replies retained
    assert ("user", "first") in roles_contents
    assert ("user", "second") in roles_contents


@pytest.mark.asyncio
async def test_ids_correlate_and_increase():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(UserInput(text="hi"))
    events = session.drain_events()
    ids = [int(e.id) for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


@pytest.mark.asyncio
async def test_shutdown_emits_shutdown_complete():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(Shutdown())
    assert _types(session.drain_events()) == ["shutdown_complete"]


@pytest.mark.asyncio
async def test_interrupt_when_idle_is_noop():
    provider = ScriptedProvider([LLMResponse(content="x", finish_reason="stop")])
    session = _session(provider)
    await session.submit(Interrupt())  # nothing running
    assert session.drain_events() == []
