"""The kernel reports the model history as it grows, in the order it grows.

A host that records a Turn as it happens needs three things from the kernel:
every message the model sees is reported before the model acts on it, a
message is never written over once reported, and a Turn that is stopped or
fails keeps what the model already saw and did.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.agent_runtime.injections import SubagentMessage
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.events import AgentSession, Interrupt, UserInput
from core.providers.base import LLMResponse, ToolCallRequest


class _Model:
    def __init__(self, replies: list[LLMResponse | Exception]) -> None:
        self.replies = list(replies)

    def get_default_model(self) -> str:
        return "scripted"

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@tool_parameters({"type": "object", "properties": {}})
class _Probe(Tool):
    """Remembers what had been reported when it ran; may then block."""

    def __init__(self, reports: list[list[dict[str, Any]]], *, block: bool) -> None:
        self.reports = reports
        self.block = block
        self.seen: list[dict[str, Any]] | None = None
        self.running = asyncio.Event()

    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "Probe."

    async def execute(self, **_kwargs: Any) -> Any:
        self.seen = self.reports[-1] if self.reports else None
        self.running.set()
        if self.block:
            await asyncio.Event().wait()
        return "probed"


def _calls_probe(text: str = "Let me check.") -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[ToolCallRequest(id="call-probe", name="probe", arguments={})],
        finish_reason="tool_calls",
    )


def _session(
    replies: list[LLMResponse | Exception],
    *,
    block: bool = False,
    **kwargs: Any,
) -> tuple[AgentSession, _Probe, list[list[dict[str, Any]]]]:
    reports: list[list[dict[str, Any]]] = []
    probe = _Probe(reports, block=block)
    tools = ToolRegistry()
    tools.register(probe)
    session = AgentSession(
        _Model(replies),
        tools,
        model="scripted",
        history_sink=lambda history: reports.append(list(history)),
        **kwargs,
    )
    return session, probe, reports


def _shape(history: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (
            str(message.get("role")),
            "call" if message.get("tool_calls") else str(message.get("content")),
        )
        for message in history
    ]


async def _run(session: AgentSession, text: str) -> list[Any]:
    return [event async for event in session.run_stream(UserInput(text=text))]


@pytest.mark.asyncio
async def test_a_tool_call_is_reported_before_the_tool_runs() -> None:
    session, probe, reports = _session([_calls_probe(), LLMResponse(content="done")])

    await _run(session, "check it")

    assert probe.seen is not None
    assert _shape(probe.seen)[-2:] == [("user", "check it"), ("assistant", "call")]
    assert _shape(reports[-1])[-4:] == [
        ("user", "check it"),
        ("assistant", "call"),
        ("tool", "probed"),
        ("assistant", "done"),
    ]


@pytest.mark.asyncio
async def test_a_stopped_turn_keeps_what_the_model_did() -> None:
    session, probe, reports = _session([_calls_probe()], block=True)
    stream = session.run_stream(UserInput(text="check it"))
    turn = asyncio.ensure_future(_drain(stream))
    await asyncio.wait_for(probe.running.wait(), 2)

    await session.submit(Interrupt())
    events = await asyncio.wait_for(turn, 2)

    assert events[-1].msg.stop_reason == "interrupted"
    assert _shape(session.history)[-2:] == [
        ("user", "check it"),
        ("assistant", "call"),
    ]
    assert _shape(reports[-1]) == _shape(session.history)


@pytest.mark.asyncio
async def test_a_failed_turn_keeps_what_the_model_did() -> None:
    session, _probe, _reports = _session(
        [_calls_probe(), RuntimeError("connection reset")]
    )

    events = await _run(session, "check it")

    assert events[-1].msg.stop_reason == "error"
    assert _shape(session.history)[-3:] == [
        ("user", "check it"),
        ("assistant", "call"),
        ("tool", "probed"),
    ]


@pytest.mark.asyncio
async def test_input_that_follows_the_prompt_stays_a_message_of_its_own() -> None:
    pending = [{"role": "user", "content": "and also this"}]

    async def inject() -> list[dict[str, Any]]:
        drained, pending[:] = list(pending), []
        return drained

    session, _probe, _reports = _session(
        [LLMResponse(content="done")], injection_callback=inject
    )

    await _run(session, "check it")

    assert _shape(session.history)[-3:] == [
        ("user", "check it"),
        ("user", "and also this"),
        ("assistant", "done"),
    ]


@pytest.mark.asyncio
async def test_a_message_that_arrives_after_the_answer_is_noted_after_it() -> None:
    result = SubagentMessage(
        message_id="result-1",
        target_turn_id="turn-1",
        agent_id="worker",
        payload="worker finished",
    )
    drains: list[int] = []

    async def inject(**_kwargs: Any) -> list[Any]:
        # Nothing before the first answer; the result lands right after it.
        drains.append(1)
        return [result] if len(drains) == 2 else []

    log: list[tuple[str, Any]] = []
    session = AgentSession(
        _Model([LLMResponse(content="first answer"), LLMResponse(content="done")]),
        ToolRegistry(),
        model="scripted",
        injection_callback=inject,
        history_sink=lambda history: log.append(("history", _shape(history))),
        context_note_sink=lambda content, source: log.append(("note", source)),
    )

    await _run(session, "check it")

    noted = log.index(("note", "subagent"))
    reported_before = [entry for kind, entry in log[:noted] if kind == "history"]
    assert reported_before[-1][-1] == ("assistant", "first answer")


async def _drain(stream) -> list[Any]:
    return [event async for event in stream]
