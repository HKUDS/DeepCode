from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.approval import ApprovalStatus
from core.domain.approval import Approval, ApprovalCategory
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import ClientSurface, TurnInputSource
from core.domain.thread import ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    Event,
    ModelUsageRecorded,
    PlanStep,
    PlanStepStatus,
    PlanUpdated,
    TaskComplete,
    ToolActivity,
    ToolActivityKind,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
)
from core.persistence.execution_repository import (
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
)
from core.persistence.event_repository import EventRepository
from core.persistence.thread_repository import ThreadRepository
from core.reasoning import ReasoningAvailability, ReasoningChannel, ReasoningPayload


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


class InterleavedSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event(
            "2",
            AgentMessageDelta(
                "I will inspect the repository.",
                message_id="commentary-1",
            ),
        )
        yield Event(
            "3",
            AgentMessageCompleted(
                message_id="commentary-1",
                text="I will inspect the repository.",
            ),
        )
        yield Event(
            "4",
            ToolStarted(
                "read-1",
                "read",
                "src/app.py",
                ToolActivity(ToolActivityKind.READ, "Read", "src/app.py"),
            ),
        )
        yield Event("5", ToolCompleted("read-1", "read", False, "contents"))
        yield Event(
            "6",
            AgentMessageDelta(
                "The repository is ready.",
                message_id="final-1",
            ),
        )
        yield Event(
            "7",
            AgentMessageCompleted(
                message_id="final-1",
                text="The repository is ready.",
            ),
        )
        yield Event(
            "8",
            AgentMessage(
                "The repository is ready.",
                message_id="final-1",
                phase=AgentMessagePhase.FINAL_ANSWER,
            ),
        )
        yield Event("9", TaskComplete("The repository is ready.", "completed"))
        self.history.append(
            {"role": "assistant", "content": "The repository is ready."}
        )


class InterleavedFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = InterleavedSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


class PlannedSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event(
            "2",
            ToolStarted(
                "plan-1",
                "update_plan",
                "",
                ToolActivity(ToolActivityKind.PLAN, "Update plan"),
            ),
        )
        yield Event(
            "3",
            PlanUpdated(
                explanation="Starting",
                plan=(
                    PlanStep("Inspect", PlanStepStatus.IN_PROGRESS),
                    PlanStep("Verify", PlanStepStatus.PENDING),
                ),
            ),
        )
        yield Event("4", ToolCompleted("plan-1", "update_plan", False, "updated"))
        yield Event("5", AgentMessage("done"))
        yield Event("6", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})


class PlannedFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = PlannedSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


class ReasoningSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event("2", AgentReasoningStarted("reasoning-1", effort="high"))
        yield Event(
            "3",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.SUMMARY,
                "Checked constraints.",
            ),
        )
        yield Event(
            "4",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.PROVIDER_TRACE,
                "provider trace",
            ),
        )
        yield Event(
            "5",
            AgentReasoningCompleted(
                "reasoning-1",
                summary_text="Checked constraints.",
                trace_text="provider trace",
                availability=ReasoningAvailability.AVAILABLE,
                effort="high",
                duration_ms=1250,
            ),
        )
        yield Event("6", AgentMessage("done"))
        yield Event("7", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})


class ReasoningFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = ReasoningSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


class StreamingReasoningHangSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event("2", AgentReasoningStarted("reasoning-1", effort="medium"))
        yield Event(
            "3",
            AgentReasoningDelta(
                "reasoning-1",
                ReasoningChannel.SUMMARY,
                "Partial reasoning",
            ),
        )
        await asyncio.Event().wait()


class StreamingReasoningHangFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = StreamingReasoningHangSession(
            approval_callback,
            approval=False,
            hang=True,
        )
        self.sessions.append(session)
        return session


class FailedCommandSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event("2", ToolStarted("bash-failed", "bash", "pytest -q"))
        yield Event(
            "3",
            ToolCompleted(
                "bash-failed",
                "bash",
                True,
                "[exit 1]\n1 failed",
            ),
        )
        yield Event("4", AgentMessage("failure handled"))
        yield Event("5", TaskComplete("failure handled", "completed"))
        self.history.append({"role": "assistant", "content": "failure handled"})


class FailedCommandFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = FailedCommandSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


class UsageThenHangSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        yield Event("1", TurnStarted())
        yield Event(
            "2",
            ModelUsageRecorded(
                response_ordinal=1,
                usage={
                    "prompt_tokens": 17,
                    "completion_tokens": 8,
                    "total_tokens": 25,
                },
            ),
        )
        # A provider response has been billed; cancellation now happens while
        # later work is still active.
        await asyncio.Event().wait()


class UsageThenHangFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = UsageThenHangSession(
            approval_callback,
            approval=False,
            hang=True,
        )
        self.sessions.append(session)
        return session


class DuplicateUsageSession(ScriptedSession):
    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        usage = ModelUsageRecorded(
            response_ordinal=1,
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        )
        yield Event("1", TurnStarted())
        yield Event("2", usage)
        yield Event("3", usage)
        yield Event("4", AgentMessage("done"))
        yield Event("5", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})


class DuplicateUsageFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = DuplicateUsageSession(
            approval_callback,
            approval=False,
            hang=False,
        )
        self.sessions.append(session)
        return session


class LegacyUsageSession(ScriptedSession):
    def __init__(self, approval_callback) -> None:
        super().__init__(approval_callback, approval=False, hang=False)
        self.last_usage = {
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "total_tokens": 10,
        }


class LegacyUsageFactory(ScriptedFactory):
    def create(self, *, workspace, model, approval_callback):
        session = LegacyUsageSession(approval_callback)
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


def test_thread_event_subscription_spans_turns_and_can_be_removed(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, ScriptedFactory())
    observed: list[Event] = []
    try:
        token = application.turns.subscribe_thread_events(
            thread_id,
            observed.append,
        )

        first = application.turns.start(thread_id, prompt="first")
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        event_types = [event.msg.type for event in observed]
        assert event_types.count("turn_started") == 2
        assert event_types.count("task_complete") == 2

        application.turns.unsubscribe_thread_events(token)
        observed_count = len(observed)
        third = application.turns.start(thread_id, prompt="third")
        _wait_for(application, third.turn.id, TurnStatus.COMPLETED)
        assert len(observed) == observed_count
    finally:
        application.close()


@pytest.mark.parametrize(
    "surface",
    [
        ClientSurface.CLI,
        ClientSurface.DESKTOP,
        ClientSurface.HEADLESS,
        ClientSurface.APP_SERVER,
    ],
)
def test_turn_preserves_typed_client_surface_for_user_and_assistant(
    tmp_path: Path,
    surface: ClientSurface,
) -> None:
    application, thread_id = _application(tmp_path, ScriptedFactory())
    try:
        started = application.turns.start(
            thread_id,
            prompt=f"Run from {surface.value}",
            client_surface=surface,
        )
        snapshot = _wait_for(
            application,
            started.turn.id,
            TurnStatus.COMPLETED,
        )
        initial = snapshot.items[0]
        assert initial.payload["client"] == surface.value
        assert initial.payload["source"] == TurnInputSource.START.value

        session = application.session_store.get_session(thread_id)
        assert session is not None
        assert [message.metadata["client"] for message in session.messages] == [
            surface.value,
            surface.value,
        ]
    finally:
        application.close()


def test_one_session_preserves_each_turns_client_surface(tmp_path: Path) -> None:
    application, thread_id = _application(tmp_path, ScriptedFactory())
    try:
        cli_turn = application.turns.start(
            thread_id,
            prompt="Start in CLI",
            client_surface=ClientSurface.CLI,
        )
        _wait_for(application, cli_turn.turn.id, TurnStatus.COMPLETED)
        desktop_turn = application.turns.start(
            thread_id,
            prompt="Continue in Desktop",
            client_surface=ClientSurface.DESKTOP,
        )
        _wait_for(application, desktop_turn.turn.id, TurnStatus.COMPLETED)

        session = application.session_store.get_session(thread_id)
        assert session is not None
        assert [message.metadata["client"] for message in session.messages] == [
            "cli",
            "cli",
            "desktop",
            "desktop",
        ]
    finally:
        application.close()


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


def test_reasoning_projects_once_and_replays_from_durable_events(
    tmp_path: Path,
) -> None:
    factory = ReasoningFactory()
    application, thread_id = _application(tmp_path, factory)
    database_path = application.database.path
    turn_id = ""
    reasoning_id = ""
    try:
        started = application.turns.start(thread_id, prompt="Think carefully")
        turn_id = started.turn.id
        snapshot = _wait_for(application, turn_id, TurnStatus.COMPLETED)
        reasoning = next(
            item for item in snapshot.items if item.kind is ItemKind.REASONING
        )
        reasoning_id = reasoning.id
        payload = ReasoningPayload.from_dict(reasoning.payload)

        assert payload.summary_text == "Checked constraints."
        assert payload.trace_text == "provider trace"
        assert payload.effort == "high"
        assert payload.duration_ms == 1250
        assert payload.streaming is False
        events = [
            event
            for event in application.events.replay(thread_id, limit=1000)
            if event.item_id == reasoning.id
        ]
        assert [event.type for event in events] == [
            "item.created",
            "item.delta",
            "item.delta",
            "item.updated",
        ]
        assert [event.payload.get("reasoningChannel") for event in events[1:3]] == [
            "summary",
            "provider_trace",
        ]
    finally:
        application.close()

    reopened = DeepCodeApplication.open(database_path, session_factory=factory)
    try:
        restored = reopened.turns.read(turn_id)
        reasoning = next(item for item in restored.items if item.id == reasoning_id)
        assert (
            ReasoningPayload.from_dict(reasoning.payload).trace_text == "provider trace"
        )
    finally:
        reopened.close()


def test_interrupted_reasoning_preserves_partial_text_and_closes_live_item(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(
        tmp_path,
        StreamingReasoningHangFactory(),
    )
    try:
        started = application.turns.start(thread_id, prompt="Think for a while")
        deadline = time.monotonic() + 2
        reasoning = None
        while reasoning is None:
            assert time.monotonic() < deadline
            snapshot = application.turns.read(started.turn.id)
            reasoning = next(
                (
                    item
                    for item in snapshot.items
                    if item.kind is ItemKind.REASONING
                    and ReasoningPayload.from_dict(item.payload).summary_text
                ),
                None,
            )
            if reasoning is None:
                time.sleep(0.01)

        accepted, _ = application.turns.interrupt(thread_id, started.turn.id)
        interrupted = _wait_for(
            application,
            started.turn.id,
            TurnStatus.INTERRUPTED,
        )
        reasoning = next(item for item in interrupted.items if item.id == reasoning.id)

        assert accepted is True
        assert reasoning.status is ItemStatus.FAILED
        assert ReasoningPayload.from_dict(reasoning.payload).summary_text == (
            "Partial reasoning"
        )
        assert reasoning.payload["interrupted"] is True
    finally:
        application.close()


def test_projection_preserves_interleaved_message_and_tool_order(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, InterleavedFactory())
    try:
        started = application.turns.start(thread_id, prompt="Inspect it")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        assert [item.kind for item in snapshot.items] == [
            ItemKind.USER_MESSAGE,
            ItemKind.ASSISTANT_MESSAGE,
            ItemKind.TOOL_CALL,
            ItemKind.ASSISTANT_MESSAGE,
            ItemKind.COMPLETION,
        ]
        commentary, tool, final = snapshot.items[1:4]
        assert commentary.payload["phase"] == "commentary"
        assert commentary.payload["messageId"] == "commentary-1"
        assert tool.payload["activity"] == {
            "kind": "read",
            "label": "Read",
            "subject": "src/app.py",
        }
        assert final.payload["phase"] == "final_answer"
        assert final.payload["messageId"] == "final-1"
        assert [item.ordinal for item in snapshot.items] == [1, 2, 3, 4, 5]
    finally:
        application.close()


def test_plan_updates_replay_without_creating_transcript_items(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, PlannedFactory())
    try:
        started = application.turns.start(thread_id, prompt="Plan it")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        assert ItemKind.PLAN not in {item.kind for item in snapshot.items}
        events = application.events.replay(thread_id, limit=1000)
        plan_event = next(
            event for event in events if event.type == "turn.plan.updated"
        )
        assert plan_event.turn_id == started.turn.id
        assert plan_event.payload == {
            "plan": {
                "explanation": "Starting",
                "steps": [
                    {"step": "Inspect", "status": "in_progress"},
                    {"step": "Verify", "status": "pending"},
                ],
            }
        }
    finally:
        application.close()


def test_failed_command_event_projects_a_failed_desktop_item(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, FailedCommandFactory())
    try:
        started = application.turns.start(thread_id, prompt="Run tests")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        command = next(
            item for item in snapshot.items if item.kind is ItemKind.COMMAND_EXECUTION
        )
        assert command.status is ItemStatus.FAILED
        assert command.payload["isError"] is True
        assert command.payload["resultPreview"].startswith("[exit 1]")
    finally:
        application.close()


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

        accepted, _turn = application.turns.interrupt(thread_id, started.turn.id)
        interrupted = _wait_for(application, started.turn.id, TurnStatus.INTERRUPTED)

        assert accepted is True
        assert _turn.status is TurnStatus.INTERRUPTED
        assert interrupted.turn.stop_reason == "interrupted"
        assert factory.sessions[0].closed is False
        assert all(
            item.status not in {ItemStatus.PENDING, ItemStatus.IN_PROGRESS}
            for item in interrupted.items
        )
        canonical = application.session_store.get_session(thread_id)
        assert canonical is not None
        marker = next(
            message
            for message in canonical.messages
            if message.metadata.get("source") == "turn_interrupt"
        )
        assert marker.metadata["turnId"] == started.turn.id
        assert marker.metadata["modelVisible"] is True
        assert any(
            event.type == "turn.completed" and event.turn_id == started.turn.id
            for event in application.events.replay(thread_id)
        )
    finally:
        application.close()
    assert factory.sessions[0].closed is True


def test_interrupted_turn_persists_usage_and_accounts_it_to_active_goal(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, UsageThenHangFactory())
    try:
        goal = application.goals.create(
            thread_id,
            objective="Finish the task",
            start=False,
        )
        started = application.turns.start(thread_id, prompt="Begin")
        deadline = time.monotonic() + 2
        while not any(
            event.type == "turn.usage.recorded" and event.turn_id == started.turn.id
            for event in application.events.replay(thread_id, limit=1000)
        ):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        application.turns.interrupt(thread_id, started.turn.id)
        snapshot = _wait_for(
            application,
            started.turn.id,
            TurnStatus.INTERRUPTED,
        )

        completion = next(
            item for item in snapshot.items if item.kind is ItemKind.COMPLETION
        )
        assert completion.payload["usage"] == {
            "prompt_tokens": 17,
            "completion_tokens": 8,
            "total_tokens": 25,
        }
        updated_goal = application.goals.read(thread_id)
        deadline = time.monotonic() + 2
        while updated_goal is not None and updated_goal.tokens_used != 25:
            assert time.monotonic() < deadline
            time.sleep(0.01)
            updated_goal = application.goals.read(thread_id)
        assert updated_goal is not None
        assert updated_goal.id == goal.id
        assert updated_goal.tokens_used == 25
        assert updated_goal.status.value == "active"
    finally:
        application.close()


def test_duplicate_response_usage_is_idempotent(tmp_path: Path) -> None:
    application, thread_id = _application(tmp_path, DuplicateUsageFactory())
    try:
        started = application.turns.start(thread_id, prompt="Count once")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        completion = next(
            item for item in snapshot.items if item.kind is ItemKind.COMPLETION
        )
        assert completion.payload["usage"]["total_tokens"] == 10
        usage_events = [
            event
            for event in application.events.replay(thread_id, limit=1000)
            if event.type == "turn.usage.recorded"
        ]
        assert len(usage_events) == 1
    finally:
        application.close()


def test_legacy_session_usage_remains_compatible_without_incremental_events(
    tmp_path: Path,
) -> None:
    application, thread_id = _application(tmp_path, LegacyUsageFactory())
    try:
        started = application.turns.start(thread_id, prompt="Legacy adapter")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        completion = next(
            item for item in snapshot.items if item.kind is ItemKind.COMPLETION
        )
        assert completion.payload["usage"] == {
            "prompt_tokens": 6,
            "completion_tokens": 4,
            "total_tokens": 10,
        }
    finally:
        application.close()


def test_interrupt_cancels_pending_approval(tmp_path: Path) -> None:
    factory = ScriptedFactory(approval=True)
    application, thread_id = _application(tmp_path, factory)
    try:
        started = application.turns.start(thread_id, prompt="Write, then stop")
        _wait_for(application, started.turn.id, TurnStatus.WAITING_APPROVAL)

        application.turns.interrupt(thread_id, started.turn.id)
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
    goal = first.goals.create(
        thread_id,
        objective="Survive a restart",
        start=False,
    )
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
            goal_id=goal.id,
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
        EventRepository(connection).append(
            thread_id=thread_id,
            turn_id=turn.id,
            type="turn.usage.recorded",
            payload={
                "responseOrdinal": 1,
                "usage": {
                    "prompt_tokens": 31,
                    "completion_tokens": 9,
                    "total_tokens": 40,
                },
            },
        )

    recovered = DeepCodeApplication.open(database_path, session_factory=factory)
    try:
        snapshot = recovered.turns.read(turn.id)
        assert snapshot.turn.status is TurnStatus.INTERRUPTED
        assert snapshot.turn.stop_reason == "application_restarted"
        assert snapshot.approvals[0].status is ApprovalStatus.CANCELLED
        assert snapshot.items[0].status is ItemStatus.DECLINED
        assert snapshot.items[-1].kind is ItemKind.COMPLETION
        assert snapshot.items[-1].payload["usage"]["total_tokens"] == 40
        recovered_goal = recovered.goals.read(thread_id)
        assert recovered_goal is not None
        assert recovered_goal.tokens_used == 40
        assert recovered_goal.status.value == "active"
        assert recovered.threads.read(thread_id).status is ThreadStatus.IDLE
        event_types = [event.type for event in recovered.events.replay(thread_id)]
        assert "turn.recovered" in event_types
        assert "thread.status_changed" in event_types
        assert event_types[-1] == "goal.updated"
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

        assert application.turns.interrupt(third_thread.id, third.turn.id)[0] is True
        _wait_for(application, third.turn.id, TurnStatus.INTERRUPTED)

        application.turns.interrupt(first_thread.id, first.turn.id)
        _wait_for(application, first.turn.id, TurnStatus.INTERRUPTED)
        _wait_for(application, second.turn.id, TurnStatus.RUNNING)
        application.turns.interrupt(second_thread.id, second.turn.id)
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
