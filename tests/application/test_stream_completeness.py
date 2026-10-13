"""Streamed text reaches clients completely, in order, before what follows it.

Streamed text goes to clients as live deltas and is not written to the
database until its item completes. The projector throttles deltas (time and
size), so these tests drive short bursts inside the throttle window and check
that the held tail still goes out: before the item completes, before another
stream starts, when the Turn is interrupted, and when the model pauses.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.application import DeepCodeApplication
from core.application import turn_projection
from core.application.live_items import LiveDelta
from core.domain import TrustState
from core.domain.item import ItemKind, ItemStatus
from core.domain.turn import TurnStatus
from core.persistence import execution_repository
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


def _durable_types(application, thread_id, item_id):
    return [
        event.type
        for event in application.events.replay(thread_id, limit=1000)
        if event.item_id == item_id
    ]


def _started(application, thread_id, prompt):
    """Start a Turn, recording everything clients are sent from now on."""

    token = application.broker.subscribe(capacity=100_000)
    turn_id = application.turns.start(thread_id, prompt=prompt).turn.id
    return turn_id, lambda: application.broker.drain(token).events


def _finished(application, turn_id, status=TurnStatus.COMPLETED):
    return _wait(
        lambda: (
            (snapshot := application.turns.read(turn_id)).turn.status is status
            and snapshot
        )
    )


def _shown(sent, item_id, *, field="text", channel="text", until=None):
    """The text a client shows from the item's creation and its live deltas.

    ``until`` stops at the first durable event of that type for the item.
    """

    text = ""
    for message in sent:
        if isinstance(message, LiveDelta):
            if message.item_id == item_id and message.channel == channel:
                assert message.offset == len(text.encode())
                text += message.delta
                assert message.next_offset == len(text.encode())
        elif message.item_id == item_id:
            if message.type == until:
                break
            if message.type == "item.created":
                text = message.payload["item"]["payload"][field]
    return text


def test_completion_sends_the_throttled_tail_first(tmp_path: Path) -> None:
    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("Hello ", "msg-1"))
        yield Event("3", AgentMessageDelta("world", "msg-1"))
        yield Event("4", AgentMessage("Hello world", "msg-1"))
        yield Event("5", TaskComplete("Hello world", "completed"))

    application, thread_id = _application(tmp_path, script)
    try:
        turn_id, sent = _started(application, thread_id, "Say hello")
        snapshot = _finished(application, turn_id)
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )

        # A client following the live stream has the whole text when the
        # completion arrives, so it never needs to reprint.
        assert _shown(sent(), assistant.id, until="item.updated") == "Hello world"
        assert _durable_types(application, thread_id, assistant.id) == [
            "item.created",
            "item.updated",
        ]
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
        turn_id, sent = _started(application, thread_id, "Stream then stop")
        _wait(
            lambda: any(
                item.kind is ItemKind.ASSISTANT_MESSAGE
                for item in application.turns.read(turn_id).items
            )
        )
        time.sleep(0.1)  # let the held delta be projected (still throttled)
        application.turns.interrupt(thread_id, turn_id)
        snapshot = _finished(application, turn_id, TurnStatus.INTERRUPTED)
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )

        assert assistant.payload["text"] == "Hello WORLD-TAIL"
        assert assistant.status is ItemStatus.FAILED
        assert assistant.payload["interrupted"] is True
        assert assistant.payload["streaming"] is False
        assert _shown(sent(), assistant.id, until="item.updated") == "Hello WORLD-TAIL"
    finally:
        application.close()


def test_reasoning_tail_is_sent_before_the_answer_starts(tmp_path: Path) -> None:
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
        turn_id, sent = _started(application, thread_id, "Think")
        snapshot = _finished(application, turn_id)
        reasoning = next(
            item for item in snapshot.items if item.kind is ItemKind.REASONING
        )
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        messages = sent()
        tail = next(
            index
            for index, message in enumerate(messages)
            if isinstance(message, LiveDelta)
            and message.item_id == reasoning.id
            and message.delta.endswith("R-TAIL")
        )
        answer_created = next(
            index
            for index, message in enumerate(messages)
            if not isinstance(message, LiveDelta)
            and message.item_id == assistant.id
            and message.type == "item.created"
        )

        assert tail < answer_created
        assert messages[tail].channel == "summary"
        assert (
            _shown(messages, reasoning.id, field="summaryText", channel="summary")
            == "R-head R-TAIL"
        )
    finally:
        application.close()


def test_a_paused_model_still_gets_its_held_text_out(tmp_path: Path) -> None:
    resume = asyncio.Event()

    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("Hello ", "msg-1"))
        yield Event("3", AgentMessageDelta("world", "msg-1"))  # held: too soon
        await resume.wait()  # the model pauses
        yield Event("4", AgentMessage("Hello world", "msg-1"))
        yield Event("5", TaskComplete("Hello world", "completed"))

    application, thread_id = _application(tmp_path, script)
    try:
        turn_id, sent = _started(application, thread_id, "Pause")
        seen = []
        assistant_id = _wait(
            lambda: (
                seen.extend(sent())
                or next(
                    (
                        message.item_id
                        for message in seen
                        if isinstance(message, LiveDelta) and message.delta == "world"
                    ),
                    None,
                )
            ),
            timeout=1.0,
        )
        assert _shown(seen, assistant_id) == "Hello world"
    finally:
        application.turns.interrupt(thread_id, turn_id)
        application.close()


def test_streamed_text_counts_utf8_bytes_and_is_written_once(
    tmp_path: Path, monkeypatch
) -> None:
    pieces = ["你好，", "world ", "😀", "!"] * 40

    async def script():
        yield Event("1", TurnStarted())
        for piece in pieces:
            yield Event("d", AgentMessageDelta(piece, "msg-1"))
            await asyncio.sleep(0.003)
        yield Event("m", AgentMessage("".join(pieces), "msg-1"))
        yield Event("done", TaskComplete("".join(pieces), "completed"))

    writes = []
    update = execution_repository.ItemRepository.update
    monkeypatch.setattr(
        execution_repository.ItemRepository,
        "update",
        lambda self, item: writes.append(item.id) or update(self, item),
    )
    application, thread_id = _application(tmp_path, script)
    try:
        turn_id, sent = _started(application, thread_id, "Stream")
        snapshot = _finished(application, turn_id)
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        messages = sent()

        assert sum(isinstance(message, LiveDelta) for message in messages) > 3
        assert _shown(messages, assistant.id, until="item.updated") == "".join(pieces)
        # Once, when it completed; never once per batch.
        assert writes.count(assistant.id) == 1
    finally:
        application.close()


async def _until(flag: threading.Event) -> None:
    """Wait on the Turn's loop for a flag the test thread sets."""

    while not flag.is_set():
        await asyncio.sleep(0.01)


def test_reads_show_text_still_streaming(tmp_path: Path) -> None:
    release = threading.Event()

    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("first ", "msg-1"))
        await asyncio.sleep(0.06)
        yield Event("3", AgentMessageDelta("second", "msg-1"))
        await asyncio.sleep(0.06)
        yield Event("4", AgentMessageDelta(" third", "msg-1"))
        await _until(release)
        yield Event("5", AgentMessage("first second third", "msg-1"))
        yield Event("6", TaskComplete("first second third", "completed"))

    application, thread_id = _application(tmp_path, script)
    try:
        turn_id = application.turns.start(thread_id, prompt="Stream").turn.id
        assistant = _wait(
            lambda: next(
                (
                    item
                    for item in application.turns.read(turn_id).items
                    if item.kind is ItemKind.ASSISTANT_MESSAGE
                    and item.payload["text"] == "first second third"
                ),
                None,
            )
        )
        assert assistant.status is ItemStatus.IN_PROGRESS
        with application.database.read() as connection:
            row = execution_repository.ItemRepository(connection).get(assistant.id)
        assert row.payload["text"] == "first "  # the row waits for completion
    finally:
        release.set()
        application.close()


def test_a_locked_database_neither_stalls_the_stream_nor_loses_text(
    tmp_path: Path, monkeypatch
) -> None:
    # Every batch now tries a checkpoint; none may wait for the lock.
    monkeypatch.setattr(turn_projection, "_CHECKPOINT_INTERVAL_S", 0.0)
    locked, release = threading.Event(), threading.Event()
    during = [f"during-{index} " for index in range(5)]

    async def script():
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta("before ", "msg-1"))
        await _until(locked)
        for piece in during:
            await asyncio.sleep(0.06)
            yield Event("d", AgentMessageDelta(piece, "msg-1"))
        await _until(release)
        text = "before " + "".join(during)
        yield Event("m", AgentMessage(text, "msg-1"))
        yield Event("done", TaskComplete(text, "completed"))

    application, thread_id = _application(tmp_path, script)
    holder = sqlite3.connect(application.database.path, isolation_level=None)
    try:
        turn_id, sent = _started(application, thread_id, "Stream")
        _wait(
            lambda: any(
                item.kind is ItemKind.ASSISTANT_MESSAGE
                for item in application.turns.read(turn_id).items
            )
        )
        holder.execute("BEGIN IMMEDIATE")
        locked_at = time.monotonic()
        locked.set()
        seen = []
        _wait(
            lambda: (
                seen.extend(sent())
                or any(
                    isinstance(message, LiveDelta) and message.delta == during[-1]
                    for message in seen
                )
            ),
            timeout=2.0,
        )
        # The busy timeout is 10 s: a checkpoint that waited would show here.
        assert time.monotonic() - locked_at < 2.0
        holder.execute("ROLLBACK")
        release.set()
        snapshot = _finished(application, turn_id)
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        assert assistant.payload["text"] == "before " + "".join(during)
        # A skipped checkpoint is not an update the transcript lost.
        assert not [item for item in snapshot.items if item.kind is ItemKind.ERROR]
    finally:
        release.set()
        holder.close()
        application.close()
