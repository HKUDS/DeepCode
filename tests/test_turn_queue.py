"""Durable App Server Turn queue semantics."""

import asyncio
import threading
import time
from dataclasses import replace

from core.application import DeepCodeApplication
from core.domain import (
    Item,
    ItemKind,
    ItemStatus,
    ThreadStatus,
    TrustState,
    Turn,
    TurnStatus,
)
from core.domain.common import utc_now
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence import Database
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore


class _QueuedAgent:
    def __init__(self, factory: "_QueueFactory") -> None:
        self.factory = factory
        self._history = []

    @property
    def history(self):
        return list(self._history)

    def load_history(self, messages) -> None:
        self._history = list(messages)

    async def run_stream(self, op):
        self.factory.prompts.append(op.text)
        yield Event("1", TurnStarted())
        if len(self.factory.prompts) == 1:
            self.factory.first_started.set()
            while not self.factory.release_first.is_set():
                await asyncio.sleep(0.01)
        elif self.factory.block_after_first:
            self.factory.second_started.set()
            while not self.factory.release_second.is_set():
                await asyncio.sleep(0.01)
        answer = f"completed: {op.text}"
        yield Event("2", AgentMessage(answer))
        yield Event("3", TaskComplete(answer, "completed"))

    async def aclose(self) -> None:
        return None


class _QueueFactory:
    def __init__(self, *, block_after_first: bool = False) -> None:
        self.prompts: list[str] = []
        self.block_after_first = block_after_first
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.second_started = threading.Event()
        self.release_second = threading.Event()

    def create(self, *, workspace, model, approval_callback):
        del workspace, model, approval_callback
        return _QueuedAgent(self)


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _application(tmp_path, *, block_after_first: bool = False):
    factory = _QueueFactory(block_after_first=block_after_first)
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Queue")
    return application, factory, thread


def test_queued_turn_runs_after_the_current_turn(tmp_path) -> None:
    application, factory, thread = _application(tmp_path)
    try:
        first = application.turns.start(thread.id, prompt="first")
        assert factory.first_started.wait(timeout=2)
        queued = application.turns.enqueue(thread.id, prompt="second")
        assert queued.turn.status.value == "queued"

        factory.release_first.set()
        _wait_for(
            lambda: (
                application.turns.read(queued.turn.id).turn.status.value == "completed"
            )
        )
        assert factory.prompts == ["first", "second"]
        assert application.turns.read(first.turn.id).turn.status.value == "completed"
    finally:
        factory.release_first.set()
        application.close()


def test_queued_turn_can_be_cancelled_without_stopping_current_work(tmp_path) -> None:
    application, factory, thread = _application(tmp_path)
    try:
        application.turns.start(thread.id, prompt="first")
        assert factory.first_started.wait(timeout=2)
        queued = application.turns.enqueue(thread.id, prompt="cancel me")

        accepted, interrupted = application.turns.interrupt(
            thread.id,
            queued.turn.id,
        )
        assert accepted is True
        assert interrupted.status.value == "interrupted"
        snapshot = application.turns.read(queued.turn.id)
        assert snapshot.items[-1].kind.value == "completion"
        assert application.threads.read(thread.id).status.value == "running"

        factory.release_first.set()
        _wait_for(lambda: application.threads.read(thread.id).status.value == "idle")
        assert factory.prompts == ["first"]
    finally:
        factory.release_first.set()
        application.close()


def test_user_queue_precedes_goal_continuation_and_interrupt_keeps_goal_active(
    tmp_path,
) -> None:
    application, factory, thread = _application(
        tmp_path,
        block_after_first=True,
    )
    try:
        goal = application.goals.create(
            thread.id,
            objective="Preserve the public API",
            start=False,
        )
        first = application.turns.start(thread.id, prompt="first Goal turn")
        assert first.turn.goal_id == goal.id
        assert factory.first_started.wait(timeout=2)

        queued = application.turns.enqueue(
            thread.id,
            prompt="user correction queued explicitly",
        )
        assert queued.turn.goal_id == goal.id
        factory.release_first.set()
        assert factory.second_started.wait(timeout=2)

        active = application.turns.active_for_thread(thread.id)
        assert active is not None
        assert active.id == queued.turn.id
        assert factory.prompts == [
            "first Goal turn",
            "user correction queued explicitly",
        ]
        assert application.goals.read(thread.id).objective == goal.objective

        accepted, interrupted = application.turns.interrupt(
            thread.id,
            queued.turn.id,
        )
        assert accepted is True
        assert interrupted.status is TurnStatus.INTERRUPTED
        time.sleep(0.05)
        assert len(factory.prompts) == 2
        assert application.goals.read(thread.id).status.value == "active"
    finally:
        factory.release_first.set()
        factory.release_second.set()
        application.close()


def test_queued_turn_survives_process_recovery(tmp_path) -> None:
    database_path = tmp_path / "state.sqlite3"
    bootstrap = DeepCodeApplication.open(database_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = bootstrap.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = bootstrap.threads.start(project.id, title="Recovery queue")
    bootstrap.close()

    database = Database(database_path)
    now = utc_now()
    running = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="lost live turn",
        status=TurnStatus.RUNNING,
        started_at=now,
    )
    queued = Turn(
        thread_id=thread.id,
        ordinal=2,
        prompt="resume after restart",
    )
    with database.transaction() as connection:
        turns = TurnRepository(connection)
        turns.add(running)
        turns.add(queued)
        items = ItemRepository(connection)
        for value in (running, queued):
            items.add(
                Item(
                    thread_id=thread.id,
                    turn_id=value.id,
                    ordinal=1,
                    kind=ItemKind.USER_MESSAGE,
                    status=ItemStatus.COMPLETED,
                    summary=value.prompt,
                    payload={"text": value.prompt},
                    created_at=now,
                    updated_at=now,
                )
            )
        threads = ThreadRepository(connection)
        threads.update(replace(thread, status=ThreadStatus.RUNNING, updated_at=now))

    factory = _QueueFactory()
    factory.release_first.set()
    recovered = DeepCodeApplication.open(
        database_path,
        session_factory=factory,
    )
    try:
        _wait_for(
            lambda: recovered.turns.read(queued.id).turn.status.value == "completed"
        )
        assert recovered.turns.read(running.id).turn.status.value == "interrupted"
        assert recovered.turns.read(running.id).items[-1].kind.value == "completion"
        assert factory.prompts == ["resume after restart"]
    finally:
        recovered.close()


def test_crash_recovery_does_not_replay_unknown_goal_work(tmp_path) -> None:
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"
    bootstrap = DeepCodeApplication.open(
        database_path,
        session_store=SessionStore(session_root),
    )
    workspace = tmp_path / "goal-workspace"
    workspace.mkdir()
    project = bootstrap.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = bootstrap.threads.start(project.id, title="Goal crash recovery")
    goal = bootstrap.goals.create(
        thread.id,
        objective="Finish without replaying unknown side effects",
        start=False,
    )
    bootstrap.close()

    database = Database(database_path)
    now = utc_now()
    lost = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="work whose side effects are unknown after the crash",
        goal_id=goal.id,
        status=TurnStatus.RUNNING,
        started_at=now,
    )
    with database.transaction() as connection:
        TurnRepository(connection).add(lost)
        ItemRepository(connection).add(
            Item(
                thread_id=thread.id,
                turn_id=lost.id,
                ordinal=1,
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=lost.prompt,
                payload={"text": lost.prompt},
                created_at=now,
                updated_at=now,
            )
        )
        ThreadRepository(connection).update(
            replace(thread, status=ThreadStatus.RUNNING, updated_at=now)
        )

    factory = _QueueFactory()
    recovered = DeepCodeApplication.open(
        database_path,
        session_factory=factory,
        session_store=SessionStore(session_root),
    )
    try:
        assert recovered.turns.read(lost.id).turn.status is TurnStatus.INTERRUPTED
        assert factory.prompts == []
        current_goal = recovered.goals.read(thread.id)
        assert current_goal is not None
        assert current_goal.id == goal.id
        assert current_goal.status.value == "active"
        assert current_goal.tokens_used == 0

        explicit = recovered.turns.start(
            thread.id,
            prompt="continue from the recovered durable evidence",
        )
        assert explicit.turn.goal_id == goal.id
        assert factory.first_started.wait(timeout=2)
        assert factory.prompts == ["continue from the recovered durable evidence"]

        accepted, interrupted = recovered.turns.interrupt(
            thread.id,
            explicit.turn.id,
        )
        assert accepted is True
        assert interrupted.status is TurnStatus.INTERRUPTED
        assert recovered.goals.read(thread.id).status.value == "active"
    finally:
        factory.release_first.set()
        recovered.close()
