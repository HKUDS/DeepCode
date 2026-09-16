from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import time

import pytest

from core.agent_runtime.injections import runtime_input_to_provider_message
from core.application import DeepCodeApplication
from core.application.errors import (
    DuplicateMessageConflictError,
    EmptyInputError,
    ExpectedTurnMismatchError,
    InputDeliveryPendingError,
    InputDeliveryUncertainError,
    InputTooLargeError,
    NoActiveTurnError,
    TurnAlreadyRunningError,
    TurnNotSteerableError,
)
from core.domain import ItemKind, TrustState, TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository


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


@pytest.fixture
def live_input(tmp_path):
    factory = _SteeringFactory()
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_factory=factory)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = app.threads.start(project.id, title="Delivery confirmation")
    turn = app.turns.start(thread.id, prompt="Initial task").turn
    try:
        assert factory.started.wait(3)
        yield app, factory, thread.id, turn.id
    finally:
        factory.release.set()
        app.close()


def _steer(app, thread_id, turn_id):
    return app.turns.steer(
        thread_id,
        expected_turn_id=turn_id,
        prompt="Preserve compatibility",
        message_id="stable-steer",
    )


def _steered_events(app, thread_id, turn_id):
    with app.database.read() as connection:
        return EventRepository(connection).list_for_turn(
            thread_id, turn_id, event_type="turn.steered"
        )


def test_concurrent_retry_observes_pending_until_delivery_confirmed(
    live_input, monkeypatch
):
    app, factory, thread_id, turn_id = live_input
    service = app.turns.turn_inputs
    append = service._append_canonical_input
    entered, release = threading.Event(), threading.Event()

    def pause(item):
        entered.set()
        assert release.wait(5)
        append(item)

    monkeypatch.setattr(service, "_append_canonical_input", pause)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(_steer, app, thread_id, turn_id)
        try:
            assert entered.wait(3)
            assert (
                app.turns.read_input(thread_id, "stable-steer").payload["deliveryState"]
                == "pending"
            )
            assert _steered_events(app, thread_id, turn_id) == []
            with pytest.raises(InputDeliveryPendingError) as pending:
                _steer(app, thread_id, turn_id)
            assert pending.value.retryable
        finally:
            release.set()
        assert not first.result(3).duplicate
    assert _steer(app, thread_id, turn_id).duplicate
    assert (
        app.turns.read_input(thread_id, "stable-steer").payload["deliveryState"]
        == "accepted"
    )
    assert len(_steered_events(app, thread_id, turn_id)) == 1
    factory.release.set()
    _wait_for(lambda: app.turns.read(turn_id).turn.status.is_terminal)
    assert factory.injected == ["Preserve compatibility"]


@pytest.mark.parametrize(
    "stage",
    [
        "intent",
        "canonical_before",
        "canonical_after",
        "commit_before",
        "commit_after",
        "confirmation",
        "publication",
    ],
)
def test_delivery_failures_do_not_acknowledge_or_repeat_input(
    live_input, monkeypatch, stage
):
    app, factory, thread_id, turn_id = live_input
    service = app.turns.turn_inputs
    if stage == "intent":
        target, method = EventRepository, "append"
    elif stage.startswith("canonical"):
        target, method = app.session_store, "append_message"
    elif stage.startswith("commit"):
        target, method = service.session_runtimes, "commit_input"
    elif stage == "confirmation":
        target, method = ItemRepository, "update"
    else:
        target, method = service, "_publish"
    original = getattr(target, method)

    def fail(*args, **kwargs):
        if (
            stage == "confirmation"
            and args[1].payload.get("deliveryState") != "accepted"
        ):
            return original(*args, **kwargs)
        if stage == "publication" and not any(
            event.type == "turn.steered" for event in args[0]
        ):
            return original(*args, **kwargs)
        if stage.endswith("after") or stage == "intent":
            original(*args, **kwargs)
        raise OSError("injected storage/delivery failure")

    with monkeypatch.context() as patch:
        patch.setattr(target, method, fail)
        with pytest.raises(
            OSError if stage == "intent" else InputDeliveryUncertainError
        ):
            _steer(app, thread_id, turn_id)
    item = app.turns.read_input(thread_id, "stable-steer")
    if stage == "intent":
        assert item is None
        assert not _steer(app, thread_id, turn_id).duplicate
    elif stage == "publication":
        assert item.payload["deliveryState"] == "accepted"
        assert _steer(app, thread_id, turn_id).duplicate
    else:
        assert item.payload["deliveryState"] == "unknown"
        with pytest.raises(InputDeliveryUncertainError) as uncertain:
            _steer(app, thread_id, turn_id)
        assert not uncertain.value.retryable
        assert _steered_events(app, thread_id, turn_id) == []
    factory.release.set()
    _wait_for(lambda: app.turns.read(turn_id).turn.status.is_terminal)
    delivered = stage in {"intent", "commit_after", "confirmation", "publication"}
    assert factory.injected == (["Preserve compatibility"] if delivered else [])
    snapshot = app.turns.read(turn_id)
    assert (
        sum(item.payload.get("messageId") == "stable-steer" for item in snapshot.items)
        == 1
    )


@pytest.mark.parametrize("state", [None, "pending"])
def test_legacy_and_abandoned_receipts_are_uncertain(live_input, state):
    app, factory, thread_id, turn_id = live_input
    _steer(app, thread_id, turn_id)
    item = app.turns.read_input(thread_id, "stable-steer")
    payload = {
        key: value for key, value in item.payload.items() if key != "deliveryState"
    }
    if state:
        payload["deliveryState"] = state
    with app.database.transaction() as connection:
        ItemRepository(connection).update(replace(item, payload=payload))
    factory.release.set()
    _wait_for(lambda: app.turns.read(turn_id).turn.status.is_terminal)
    assert (
        app.turns.read_input(thread_id, "stable-steer").payload["deliveryState"]
        == "unknown"
    )
    with pytest.raises(InputDeliveryUncertainError):
        _steer(app, thread_id, turn_id)
    if state:
        with app.database.read() as connection:
            assert (
                ItemRepository(connection).get(item.id).payload["deliveryState"]
                == "unknown"
            )


@pytest.mark.parametrize("rebuild", [False, True])
def test_steer_receipt_restart_preserves_only_confirmed_evidence(
    live_input, tmp_path, rebuild
):
    app, factory, thread_id, turn_id = live_input
    _steer(app, thread_id, turn_id)
    factory.release.set()
    _wait_for(lambda: app.turns.read(turn_id).turn.status.is_terminal)
    app.close()
    reopened = DeepCodeApplication.open(
        tmp_path / ("rebuilt.sqlite3" if rebuild else "state.sqlite3"),
        session_factory=_SteeringFactory(),
    )
    try:
        item = reopened.turns.read_input(thread_id, "stable-steer")
        assert item.payload["expectedTurnId"] == turn_id
        assert item.payload["deliveryState"] == ("unknown" if rebuild else "accepted")
        if rebuild:
            with pytest.raises(InputDeliveryUncertainError):
                _steer(reopened, thread_id, turn_id)
        else:
            assert _steer(reopened, thread_id, turn_id).duplicate
    finally:
        reopened.close()


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
