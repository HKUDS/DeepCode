from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.approval import ApprovalStatus
from core.domain.approval import Approval, ApprovalCategory
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.thread import ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.events import (
    AgentMessage,
    AgentMessageDelta,
    Event,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
)
from core.persistence.execution_repository import (
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
)
from core.persistence.thread_repository import ThreadRepository


class ScriptedSession:
    def __init__(self, approval_callback, *, approval: bool, hang: bool) -> None:
        self.approval_callback = approval_callback
        self.approval = approval
        self.hang = hang
        self.history: list[dict[str, Any]] = []
        self.closed = False

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = messages

    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        if self.hang:
            await asyncio.Event().wait()
        if self.approval:
            yield Event("2", ToolStarted("call-1", "write", "write a.py"))
            approved = await self.approval_callback(
                "write", {"file_path": "a.py", "content": "x"}, "mutating tool"
            )
            yield Event(
                "3",
                ToolCompleted(
                    "call-1",
                    "write",
                    not approved,
                    "written" if approved else "denied",
                ),
            )
        yield Event("4", AgentMessageDelta("do"))
        yield Event("5", AgentMessageDelta("ne"))
        yield Event("6", AgentMessage("done"))
        yield Event("7", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})

    async def aclose(self) -> None:
        self.closed = True


class ScriptedFactory:
    def __init__(self, *, approval: bool = False, hang: bool = False) -> None:
        self.approval = approval
        self.hang = hang
        self.sessions: list[ScriptedSession] = []

    def create(self, *, workspace, model, approval_callback):
        session = ScriptedSession(
            approval_callback, approval=self.approval, hang=self.hang
        )
        self.sessions.append(session)
        return session


class LongStreamingSession(ScriptedSession):
    async def run_stream(self, _op):
        first = "a" * 300
        second = "b" * 300
        final = first + second
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessageDelta(first))
        yield Event("3", AgentMessageDelta(second))
        yield Event("4", AgentMessage(final))
        yield Event("5", TaskComplete(final, "completed"))
        self.history.append({"role": "assistant", "content": final})


class LongStreamingFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = LongStreamingSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


def _application(
    tmp_path: Path, factory: ScriptedFactory
) -> tuple[DeepCodeApplication, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=factory
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Agent thread")
    return application, thread.id


def _wait_for(application: DeepCodeApplication, turn_id: str, status) -> Any:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"turn did not reach {status}: {application.turns.read(turn_id).turn}"
    )


def test_turn_projects_stream_into_durable_items(tmp_path: Path) -> None:
    factory = ScriptedFactory()
    application, thread_id = _application(tmp_path, factory)
    try:
        started = application.turns.start(thread_id, prompt="Build it")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        assert [item.kind for item in snapshot.items] == [
            ItemKind.USER_MESSAGE,
            ItemKind.ASSISTANT_MESSAGE,
            ItemKind.COMPLETION,
        ]
        assert snapshot.items[1].payload["text"] == "done"
        assert snapshot.items[1].status is ItemStatus.COMPLETED
        assert factory.sessions[0].closed is False
    finally:
        application.close()
    assert factory.sessions[0].closed is True


def test_streaming_projection_logs_only_new_assistant_text(tmp_path: Path) -> None:
    factory = LongStreamingFactory()
    application, thread_id = _application(tmp_path, factory)
    database_path = application.database.path
    turn_id = ""
    assistant_id = ""
    try:
        started = application.turns.start(thread_id, prompt="Stream it")
        turn_id = started.turn.id
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
        assistant = next(
            item for item in snapshot.items if item.kind is ItemKind.ASSISTANT_MESSAGE
        )
        assistant_id = assistant.id
        events = [
            event
            for event in application.events.replay(thread_id, limit=1000)
            if event.item_id == assistant.id
        ]

        assert [event.type for event in events] == [
            "item.created",
            "item.delta",
            "item.updated",
        ]
        assert events[1].payload["delta"] == "b" * 300
        assert "item" not in events[1].payload
        assert assistant.payload["text"] == ("a" * 300) + ("b" * 300)
        assert assistant.status is ItemStatus.COMPLETED
    finally:
        application.close()

    reopened = DeepCodeApplication.open(database_path, session_factory=factory)
    try:
        restored = reopened.turns.read(turn_id)
        assistant = next(item for item in restored.items if item.id == assistant_id)
        assert assistant.payload["text"] == ("a" * 300) + ("b" * 300)
        replayed = [
            event.type
            for event in reopened.events.replay(thread_id, limit=1000)
            if event.item_id == assistant_id
        ]
        assert replayed == ["item.created", "item.delta", "item.updated"]
    finally:
        reopened.close()


def test_turns_reuse_one_agent_session_until_application_close(tmp_path: Path) -> None:
    factory = ScriptedFactory()
    application, thread_id = _application(tmp_path, factory)
    try:
        first = application.turns.start(thread_id, prompt="first")
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert len(factory.sessions) == 1
        assert factory.sessions[0].history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "done"},
        ]
        assert factory.sessions[0].closed is False
    finally:
        application.close()
    assert factory.sessions[0].closed is True


def test_approval_round_trip_resumes_the_same_turn(tmp_path: Path) -> None:
    factory = ScriptedFactory(approval=True)
    application, thread_id = _application(tmp_path, factory)
    try:
        started = application.turns.start(thread_id, prompt="Write it")
        waiting = _wait_for(application, started.turn.id, TurnStatus.WAITING_APPROVAL)
        approval = waiting.approvals[0]

        resolved = application.approvals.respond(
            approval.id, decision=ApprovalStatus.APPROVED_ONCE
        )
        completed = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        assert resolved.status is ApprovalStatus.APPROVED_ONCE
        assert completed.approvals[0].status is ApprovalStatus.APPROVED_ONCE
        tool = next(
            item for item in completed.items if item.kind is ItemKind.FILE_CHANGE
        )
        assert tool.status is ItemStatus.COMPLETED
    finally:
        application.close()


def test_interrupt_cancels_background_turn_and_leaves_terminal_state(
    tmp_path: Path,
) -> None:
    factory = ScriptedFactory(hang=True)
    application, thread_id = _application(tmp_path, factory)
    try:
        started = application.turns.start(thread_id, prompt="Wait")
        _wait_for(application, started.turn.id, TurnStatus.RUNNING)

        accepted, _turn = application.turns.interrupt(started.turn.id)
        interrupted = _wait_for(application, started.turn.id, TurnStatus.INTERRUPTED)

        assert accepted is True
        assert interrupted.turn.stop_reason == "interrupted"
        assert factory.sessions[0].closed is False
        assert all(
            item.status not in {ItemStatus.PENDING, ItemStatus.IN_PROGRESS}
            for item in interrupted.items
        )
    finally:
        application.close()
    assert factory.sessions[0].closed is True


def test_interrupt_cancels_pending_approval(tmp_path: Path) -> None:
    factory = ScriptedFactory(approval=True)
    application, thread_id = _application(tmp_path, factory)
    try:
        started = application.turns.start(thread_id, prompt="Write, then stop")
        _wait_for(application, started.turn.id, TurnStatus.WAITING_APPROVAL)

        application.turns.interrupt(started.turn.id)
        interrupted = _wait_for(application, started.turn.id, TurnStatus.INTERRUPTED)

        assert interrupted.approvals[0].status is ApprovalStatus.CANCELLED
        approval_item = next(
            item for item in interrupted.items if item.kind is ItemKind.APPROVAL_REQUEST
        )
        assert approval_item.status is ItemStatus.DECLINED
    finally:
        application.close()


def test_open_recovers_incomplete_turn_and_pending_approval(tmp_path: Path) -> None:
    factory = ScriptedFactory()
    first, thread_id = _application(tmp_path, factory)
    database_path = first.database.path
    first.close()
    now = datetime.now(timezone.utc)
    with first.database.transaction() as connection:
        threads = ThreadRepository(connection)
        thread = threads.get(thread_id)
        assert thread is not None
        threads.update(replace(thread, status=ThreadStatus.WAITING, updated_at=now))
        turns = TurnRepository(connection)
        turn = Turn(
            thread_id=thread_id,
            ordinal=turns.next_ordinal(thread_id),
            prompt="Interrupted by crash",
            status=TurnStatus.WAITING_APPROVAL,
            started_at=now,
        )
        turns.add(turn)
        items = ItemRepository(connection)
        approval_item = Item(
            thread_id=thread_id,
            turn_id=turn.id,
            ordinal=1,
            kind=ItemKind.APPROVAL_REQUEST,
            status=ItemStatus.PENDING,
            summary="Approval required",
            created_at=now,
            updated_at=now,
        )
        items.add(approval_item)
        ApprovalRepository(connection).add(
            Approval(
                thread_id=thread_id,
                turn_id=turn.id,
                item_id=approval_item.id,
                category=ApprovalCategory.FILE_WRITE,
                request={"toolName": "write"},
                requested_at=now,
            )
        )

    recovered = DeepCodeApplication.open(database_path, session_factory=factory)
    try:
        snapshot = recovered.turns.read(turn.id)
        assert snapshot.turn.status is TurnStatus.INTERRUPTED
        assert snapshot.turn.stop_reason == "application_restarted"
        assert snapshot.approvals[0].status is ApprovalStatus.CANCELLED
        assert snapshot.items[0].status is ItemStatus.DECLINED
        assert snapshot.items[-1].kind is ItemKind.COMPLETION
        assert recovered.threads.read(thread_id).status is ThreadStatus.IDLE
        event_types = [event.type for event in recovered.events.replay(thread_id)]
        assert "turn.recovered" in event_types
        assert event_types[-1] == "thread.status_changed"
    finally:
        recovered.close()


def test_execution_registry_bounds_concurrent_turns(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = ScriptedFactory(hang=True)
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
        max_concurrent_turns=1,
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    first_thread = application.threads.start(project.id, title="First")
    second_thread = application.threads.start(project.id, title="Second")
    third_thread = application.threads.start(project.id, title="Third")
    try:
        first = application.turns.start(first_thread.id, prompt="first")
        second = application.turns.start(second_thread.id, prompt="second")
        third = application.turns.start(third_thread.id, prompt="third")
        _wait_for(application, first.turn.id, TurnStatus.RUNNING)
        time.sleep(0.05)
        assert application.turns.read(second.turn.id).turn.status is TurnStatus.QUEUED
        assert application.turns.read(third.turn.id).turn.status is TurnStatus.QUEUED

        assert application.turns.interrupt(third.turn.id)[0] is True
        _wait_for(application, third.turn.id, TurnStatus.INTERRUPTED)

        application.turns.interrupt(first.turn.id)
        _wait_for(application, first.turn.id, TurnStatus.INTERRUPTED)
        _wait_for(application, second.turn.id, TurnStatus.RUNNING)
        application.turns.interrupt(second.turn.id)
        _wait_for(application, second.turn.id, TurnStatus.INTERRUPTED)
    finally:
        application.close()


def test_application_close_drains_running_and_queued_turns(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database_path = tmp_path / "state.sqlite3"
    factory = ScriptedFactory(hang=True)
    application = DeepCodeApplication.open(
        database_path,
        session_factory=factory,
        max_concurrent_turns=1,
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    first_thread = application.threads.start(project.id, title="Running")
    second_thread = application.threads.start(project.id, title="Queued")
    first = application.turns.start(first_thread.id, prompt="run")
    second = application.turns.start(second_thread.id, prompt="queue")
    _wait_for(application, first.turn.id, TurnStatus.RUNNING)
    assert application.turns.read(second.turn.id).turn.status is TurnStatus.QUEUED

    application.close()

    reopened = DeepCodeApplication.open(database_path, session_factory=factory)
    try:
        assert reopened.turns.read(first.turn.id).turn.status is TurnStatus.INTERRUPTED
        assert reopened.turns.read(second.turn.id).turn.status is TurnStatus.INTERRUPTED
        event_types = [event.type for event in reopened.events.replay(second_thread.id)]
        assert "turn.completed" in event_types
        assert "turn.recovered" not in event_types
    finally:
        reopened.close()
