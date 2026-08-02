from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.agent_runtime.injections import runtime_input_to_provider_message
from core.application import DeepCodeApplication
from core.application.errors import (
    DuplicateMessageConflictError,
    EmptyInputError,
    ExpectedTurnMismatchError,
    InputTooLargeError,
    NoActiveTurnError,
    TurnAlreadyRunningError,
    TurnNotSteerableError,
)
from core.domain import ItemKind, TrustState, TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted


class _SteeringAgent:
    def __init__(self, factory: _SteeringFactory, injection_callback) -> None:
        self.factory = factory
        self.injection_callback = injection_callback
        self.history: list[dict] = []
        self.last_usage: dict[str, int] = {}

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("turn-started", TurnStarted())
        self.factory.started.set()
        while not self.factory.release.is_set():
            await asyncio.sleep(0.01)
        injected = await self.injection_callback(limit=10)
        provider_messages = [
            runtime_input_to_provider_message(message) for message in injected
        ]
        self.factory.injected.extend(
            str(message["content"]) for message in provider_messages
        )
        self.history.extend(provider_messages)
        answer = "completed with steering"
        self.history.append({"role": "assistant", "content": answer})
        yield Event("answer", AgentMessage(answer))
        yield Event("complete", TaskComplete(answer, "completed"))

    async def aclose(self) -> None:
        return None


class _SteeringFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.injected: list[str] = []

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        injection_callback,
    ):
        del workspace, model, approval_callback
        return _SteeringAgent(self, injection_callback)


class _ClosingAgent:
    def __init__(self, factory: _ClosingFactory, injection_callback) -> None:
        self.factory = factory
        self.injection_callback = injection_callback
        self.history: list[dict] = []
        self.last_usage: dict[str, int] = {}

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("turn-started", TurnStarted())
        self.factory.started.set()
        while not self.factory.begin_close.is_set():
            await asyncio.sleep(0.01)
        self.factory.drained = await self.injection_callback(close_if_empty=True)
        self.factory.closed.set()
        while not self.factory.finish.is_set():
            await asyncio.sleep(0.01)
        answer = "closed"
        self.history.append({"role": "assistant", "content": answer})
        yield Event("answer", AgentMessage(answer))
        yield Event("complete", TaskComplete(answer, "completed"))

    async def aclose(self) -> None:
        return None


class _ClosingFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.begin_close = threading.Event()
        self.closed = threading.Event()
        self.finish = threading.Event()
        self.drained = []

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        injection_callback,
    ):
        del workspace, model, approval_callback
        return _ClosingAgent(self, injection_callback)


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def test_live_steer_is_durable_injected_once_and_visible_in_items(tmp_path) -> None:
    factory = _SteeringFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Steering")
        started = application.turns.start(thread.id, prompt="Initial task")
        assert factory.started.wait(timeout=2)

        receipt = application.turns.steer(
            thread.id,
            expected_turn_id=started.turn.id,
            prompt="Keep the compatibility layer.",
            message_id="client-message-1",
        )
        duplicate = application.turns.steer(
            thread.id,
            expected_turn_id=started.turn.id,
            prompt="Keep the compatibility layer.",
            message_id="client-message-1",
        )
        assert receipt.delivery == "current_turn"
        assert duplicate.duplicate is True

        factory.release.set()
        _wait_for(
            lambda: (
                application.turns.read(started.turn.id).turn.status
                is TurnStatus.COMPLETED
            )
        )
        assert factory.injected == ["Keep the compatibility layer."]
        snapshot = application.turns.read(started.turn.id)
        user_items = [
            item for item in snapshot.items if item.kind is ItemKind.USER_MESSAGE
        ]
        assert [item.payload["text"] for item in user_items] == [
            "Initial task",
            "Keep the compatibility layer.",
        ]
        canonical = application.session_store.get_session(thread.id)
        assert canonical is not None
        assert [(message.role, message.content) for message in canonical.messages] == [
            ("user", "Initial task"),
            ("user", "Keep the compatibility layer."),
            ("assistant", "completed with steering"),
        ]
    finally:
        factory.release.set()
        application.close()


@pytest.mark.parametrize(
    "message",
    (
        "停止使用缓存，先检查数据库实现。",
        "改目标模块的名字，但保持当前任务目标不变。",
        "继续分析，然后告诉我你的结论。",
    ),
)
def test_control_words_in_plain_text_remain_model_input(
    tmp_path,
    message: str,
) -> None:
    """Lifecycle commands are explicit APIs, never inferred from user text."""

    factory = _SteeringFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Plain text")
        started = application.turns.start(thread.id, prompt="Inspect the project")
        assert factory.started.wait(timeout=2)

        application.turns.steer(
            thread.id,
            expected_turn_id=started.turn.id,
            prompt=message,
            message_id=f"plain-{message[:2]}",
        )
        assert application.turns.read(started.turn.id).turn.status is TurnStatus.RUNNING

        factory.release.set()
        _wait_for(
            lambda: (
                application.turns.read(started.turn.id).turn.status
                is TurnStatus.COMPLETED
            )
        )
        assert factory.injected == [message]
    finally:
        factory.release.set()
        application.close()


def test_strict_steer_never_creates_or_queues_a_turn(tmp_path) -> None:
    factory = _SteeringFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Queued steering")

        with pytest.raises(NoActiveTurnError) as missing:
            application.turns.steer(
                thread.id,
                expected_turn_id="turn_missing",
                prompt="Preserve the public API.",
                message_id="strict-message-1",
            )
        assert missing.value.code == "NO_ACTIVE_TURN"
        assert application.turns.active_for_thread(thread.id) is None
        assert application.turns.conversation_count(thread.id) == 0
        events = application.events.replay(thread.id)
        assert all(event.type != "turn.input_queued" for event in events)
    finally:
        factory.release.set()
        application.close()


def test_steer_requires_the_exact_executing_turn_and_message_id_is_stable(
    tmp_path,
) -> None:
    factory = _SteeringFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Strict steering")
        started = application.turns.start(
            thread.id,
            prompt="Initial task",
            message_id="start-message-1",
        )
        duplicate_start = application.turns.start(
            thread.id,
            prompt="Initial task",
            message_id="start-message-1",
        )
        assert duplicate_start.turn.id == started.turn.id
        assert factory.started.wait(timeout=2)
        with pytest.raises(TurnAlreadyRunningError) as busy:
            application.turns.start(
                thread.id,
                prompt="Competing task",
                message_id="start-message-2",
            )
        assert busy.value.code == "TURN_ALREADY_ACTIVE"
        assert busy.value.details["actualTurnId"] == started.turn.id

        with pytest.raises(ExpectedTurnMismatchError) as mismatch:
            application.turns.steer(
                thread.id,
                expected_turn_id="turn_stale",
                prompt="Use a different approach.",
                message_id="steer-message-1",
            )
        assert mismatch.value.details["actualTurnId"] == started.turn.id
        with pytest.raises(EmptyInputError):
            application.turns.steer(
                thread.id,
                expected_turn_id=started.turn.id,
                prompt="   ",
                message_id="empty-steer-1",
            )
        with pytest.raises(InputTooLargeError):
            application.turns.steer(
                thread.id,
                expected_turn_id=started.turn.id,
                prompt="x" * 32_001,
                message_id="large-steer-1",
            )

        other_thread = application.threads.start(project.id, title="Other thread")
        with pytest.raises(ExpectedTurnMismatchError):
            application.turns.interrupt(other_thread.id, started.turn.id)
        assert application.turns.read(started.turn.id).turn.status is TurnStatus.RUNNING

        application.turns.steer(
            thread.id,
            expected_turn_id=started.turn.id,
            prompt="Use a different approach.",
            message_id="steer-message-1",
        )
        with pytest.raises(DuplicateMessageConflictError):
            application.turns.steer(
                thread.id,
                expected_turn_id=started.turn.id,
                prompt="Use an incompatible approach.",
                message_id="steer-message-1",
            )
    finally:
        factory.release.set()
        application.close()


def test_steer_racing_after_final_close_is_rejected_and_never_carried(
    tmp_path,
) -> None:
    factory = _ClosingFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Closing race")
        started = application.turns.start(thread.id, prompt="Initial task")
        assert factory.started.wait(timeout=2)

        factory.begin_close.set()
        assert factory.closed.wait(timeout=2)
        assert factory.drained == []
        with pytest.raises(TurnNotSteerableError):
            application.turns.steer(
                thread.id,
                expected_turn_id=started.turn.id,
                prompt="Too late for this Turn.",
                message_id="late-steer-1",
            )
        assert application.turns.conversation_count(thread.id) == 1
        assert application.turns.active_for_thread(thread.id).id == started.turn.id
        assert all(
            event.type != "turn.input_queued"
            for event in application.events.replay(thread.id)
        )
    finally:
        factory.finish.set()
        application.close()
