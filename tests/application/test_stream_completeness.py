"""Streamed text reaches clients completely, in order, before what follows it.

The projector throttles deltas (time and size), so these tests drive short
bursts that stay inside the throttle window and check that the held tail is
still published: before the item completes, before another stream starts, and
when the Turn is interrupted.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.item import ItemKind, ItemStatus
from core.domain.turn import TurnStatus
from core.events import (
    AgentMessage,
    AgentMessageDelta,
    AgentReasoningDelta,
    AgentReasoningStarted,
    Event,
    TaskComplete,
    TurnStarted,
)
from core.reasoning import ReasoningChannel


class _Session:
    def __init__(self, script) -> None:
        self.script = script
        self.history: list[dict[str, Any]] = []

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = messages

    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        async for event in self.script():
            yield event

    async def aclose(self) -> None:
        return None


class _Factory:
    def __init__(self, script) -> None:
        self.script = script

    def create(self, *, workspace, model, approval_callback):
        return _Session(self.script)


def _application(tmp_path: Path, script) -> tuple[DeepCodeApplication, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=_Factory(script)
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Stream thread")
    return application, thread.id


def _wait(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _item_events(application, thread_id, item_id):
    return [
        event
        for event in application.events.replay(thread_id, limit=1000)
        if event.item_id == item_id
    ]


def _client_text(events) -> str:
    """The text a client assembles from item.created plus item.delta events."""

    text = events[0].payload["item"]["payload"]["text"]
    for event in events[1:]:
        if event.type == "item.delta":
            text += event.payload["delta"]
    return text


def test_completion_publishes_the_throttled_tail_first(tmp_path: Path) -> None:
    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("Hello ", "msg-1"))
        yield Event("3", AgentMessageDelta("world", "msg-1"))
        yield Event("4", AgentMessage("Hello world", "msg-1"))
        yield Event("5", TaskComplete("Hello world", "completed"))

    application, thread_id = _application(tmp_path, script)
    try:
        started = application.turns.start(thread_id, prompt="Say hello")
        snapshot = _wait(
            lambda: (
                (s := application.turns.read(started.turn.id)).turn.status
                is TurnStatus.COMPLETED
                and s
            )
        )
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        events = _item_events(application, thread_id, assistant.id)

        assert [event.type for event in events] == [
            "item.created",
            "item.delta",
            "item.updated",
        ]
        # A client following only created + delta already has the whole text
        # when the completion arrives, so it never needs to reprint.
        assert _client_text(events[:-1]) == "Hello world"
        assert assistant.payload["text"] == "Hello world"
    finally:
        application.close()


def test_interrupt_keeps_text_the_throttle_was_holding(tmp_path: Path) -> None:
    streamed = asyncio.Event()

    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("Hello ", "msg-1"))
        yield Event("3", AgentMessageDelta("WORLD-TAIL", "msg-1"))
        streamed.set()
        await asyncio.Event().wait()

    application, thread_id = _application(tmp_path, script)
    try:
        started = application.turns.start(thread_id, prompt="Stream then stop")
        turn_id = started.turn.id
        _wait(
            lambda: any(
                item.kind is ItemKind.ASSISTANT_MESSAGE
                for item in application.turns.read(turn_id).items
            )
        )
        time.sleep(0.1)  # let the held delta be projected (still throttled)
        application.turns.interrupt(thread_id, turn_id)
        snapshot = _wait(
            lambda: (
                (s := application.turns.read(turn_id)).turn.status
                is TurnStatus.INTERRUPTED
                and s
            )
        )
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )

        assert assistant.payload["text"] == "Hello WORLD-TAIL"
        assert assistant.status is ItemStatus.FAILED
        assert assistant.payload["interrupted"] is True
        assert assistant.payload["streaming"] is False
        events = _item_events(application, thread_id, assistant.id)
        assert _client_text(events) == "Hello WORLD-TAIL"
    finally:
        application.close()


def test_reasoning_tail_is_published_before_the_answer_starts(tmp_path: Path) -> None:
    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentReasoningStarted("r-1"))
        yield Event(
            "3", AgentReasoningDelta("r-1", ReasoningChannel.SUMMARY, "R-head ")
        )
        yield Event("4", AgentReasoningDelta("r-1", ReasoningChannel.SUMMARY, "R-TAIL"))
        yield Event("5", AgentMessageDelta("Answer", "msg-1"))
        yield Event("6", AgentMessage("Answer", "msg-1"))
        yield Event("7", TaskComplete("Answer", "completed"))

    application, thread_id = _application(tmp_path, script)
    try:
        started = application.turns.start(thread_id, prompt="Think")
        snapshot = _wait(
            lambda: (
                (s := application.turns.read(started.turn.id)).turn.status
                is TurnStatus.COMPLETED
                and s
            )
        )
        reasoning = next(
            item for item in snapshot.items if item.kind is ItemKind.REASONING
        )
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        events = application.events.replay(thread_id, limit=1000)
        tail = next(
            event
            for event in events
            if event.item_id == reasoning.id
            and event.type == "item.delta"
            and event.payload["delta"] == "R-TAIL"
        )
        answer_created = next(
            event
            for event in events
            if event.item_id == assistant.id and event.type == "item.created"
        )

        assert tail.sequence < answer_created.sequence
    finally:
        application.close()
