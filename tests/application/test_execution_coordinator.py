from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from core.application.execution_coordinator import (
    ExecutionCoordinator,
    ExecutionDispatch,
)
from core.domain import (
    ExecutionClass,
    Project,
    RuntimeWorker,
    Thread,
    ThreadMode,
    Turn,
    TurnStatus,
)
from core.domain.common import utc_now
from core.file_lock import FileLease
from core.persistence import (
    Database,
    ProjectRepository,
    RuntimeCoordinationRepository,
    ThreadRepository,
    TurnRepository,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> datetime:
        self.value += timedelta(**kwargs)
        return self.value


class FakeRegistry:
    def __init__(self) -> None:
        self.dispatches: list[ExecutionDispatch] = []

    def start(self, dispatch: ExecutionDispatch) -> None:
        self.dispatches.append(dispatch)


class FailingRegistry:
    def __init__(self) -> None:
        self.attempts = 0

    def start(self, _dispatch: ExecutionDispatch) -> None:
        self.attempts += 1
        raise RuntimeError("registry unavailable")


def _add_project(database: Database, tmp_path: Path, suffix: str) -> Project:
    project = Project(
        canonical_path=str(tmp_path / f"workspace-{suffix}"),
        display_name=f"Project {suffix}",
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
    return project


def _add_turn(
    database: Database,
    project: Project,
    *,
    suffix: str,
    home_worker_id: str | None,
    execution_class: ExecutionClass = ExecutionClass.INTERACTIVE,
    ordinal: int = 1,
    thread: Thread | None = None,
    enqueued_at: datetime | None = None,
) -> tuple[Thread, Turn]:
    selected_thread = thread or Thread(
        project_id=project.id,
        title=f"Thread {suffix}",
        mode=ThreadMode.CODE,
        workspace_path=project.canonical_path,
    )
    turn = Turn(
        thread_id=selected_thread.id,
        ordinal=ordinal,
        prompt=f"Execute {suffix}",
        execution_class=execution_class,
        home_worker_id=home_worker_id,
        enqueued_at=enqueued_at or utc_now(),
    )
    with database.transaction() as connection:
        if thread is None:
            ThreadRepository(connection).add(selected_thread)
        TurnRepository(connection).add(turn)
    return selected_thread, turn


def _finish_turn(
    database: Database,
    coordinator: ExecutionCoordinator,
    dispatch: ExecutionDispatch,
    *,
    completed_at: datetime,
) -> None:
    with database.transaction() as connection:
        assert coordinator.release_in_transaction(
            connection,
            dispatch.claim,
            reason="completed",
            released_at=completed_at,
        )
        turns = TurnRepository(connection)
        current = turns.get(dispatch.turn_id)
        assert current is not None
        turns.update(
            replace(
                current,
                status=TurnStatus.COMPLETED,
                stop_reason="completed",
                completed_at=completed_at,
            )
        )
    coordinator.confirm_released(dispatch.claim)


def test_start_registers_worker_holds_os_lock_and_heartbeats(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FakeRegistry()
    coordinator = ExecutionCoordinator(
        database,
        registry.start,
        worker_id="worker_lifecycle",
        clock=clock,
    )

    worker = coordinator.start(background=False)
    competing = FileLease.acquire(
        coordinator.worker_lock_path(worker.id),
        shared=False,
        blocking=False,
    )
    assert competing is None

    heartbeat_at = clock.advance(seconds=3)
    assert coordinator.heartbeat() == ()
    with database.read() as connection:
        persisted = RuntimeCoordinationRepository(connection).get_worker(worker.id)
    assert persisted is not None
    assert persisted.heartbeat_at == heartbeat_at
    assert persisted.stopped_at is None

    coordinator.close()
    released = FileLease.acquire(
        coordinator.worker_lock_path(worker.id),
        shared=False,
        blocking=False,
    )
    assert released is not None
    released.close()
    with database.read() as connection:
        stopped = RuntimeCoordinationRepository(connection).get_worker(worker.id)
    assert stopped is not None
    assert stopped.stopped_at == heartbeat_at


def test_same_workspace_waits_without_registry_slot_while_other_workspace_runs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FakeRegistry()
    coordinator = ExecutionCoordinator(
        database,
        registry.start,
        max_concurrent_turns=2,
        worker_id="worker_workspace",
        clock=clock,
    )
    worker = coordinator.start(background=False)
    shared = _add_project(database, tmp_path, "shared")
    separate = _add_project(database, tmp_path, "separate")
    _, first = _add_turn(
        database,
        shared,
        suffix="shared-first",
        home_worker_id=worker.id,
        enqueued_at=clock.value,
    )
    _, waiting = _add_turn(
        database,
        shared,
        suffix="shared-waiting",
        home_worker_id=worker.id,
        enqueued_at=clock.value + timedelta(milliseconds=1),
    )
    _, parallel = _add_turn(
        database,
        separate,
        suffix="parallel",
        home_worker_id=worker.id,
        enqueued_at=clock.value + timedelta(milliseconds=2),
    )

    dispatched = coordinator.dispatch_once()
    assert {entry.turn_id for entry in dispatched} == {first.id, parallel.id}
    assert {entry.turn_id for entry in registry.dispatches} == {
        first.id,
        parallel.id,
    }
    with database.read() as connection:
        waiting_turn = TurnRepository(connection).get(waiting.id)
    assert waiting_turn is not None
    assert waiting_turn.execution_owner_id is None

    by_id = {entry.turn_id: entry for entry in dispatched}
    completed_at = clock.advance(seconds=1)
    _finish_turn(
        database,
        coordinator,
        by_id[first.id],
        completed_at=completed_at,
    )
    next_dispatch = coordinator.dispatch_once()
    assert [entry.turn_id for entry in next_dispatch] == [waiting.id]
    assert coordinator.release(
        next_dispatch[0].claim,
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    assert coordinator.release(
        by_id[parallel.id].claim,
        reason="test_cleanup",
        released_at=clock.value,
    )
    coordinator.close()


def test_managed_worktree_runs_in_parallel_with_its_canonical_project(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FakeRegistry()
    coordinator = ExecutionCoordinator(
        database,
        registry.start,
        max_concurrent_turns=2,
        worker_id="worker_worktree",
        clock=clock,
    )
    worker = coordinator.start(background=False)
    project = _add_project(database, tmp_path, "worktree-isolation")
    _, canonical = _add_turn(
        database,
        project,
        suffix="canonical",
        home_worker_id=worker.id,
        enqueued_at=clock.value,
    )
    worktree_path = tmp_path / "managed-worktree"
    worktree_thread = Thread(
        project_id=project.id,
        title="Managed worktree",
        mode=ThreadMode.CODE,
        workspace_path=str(worktree_path),
        worktree_path=str(worktree_path),
    )
    with database.transaction() as connection:
        ThreadRepository(connection).add(worktree_thread)
    _, isolated = _add_turn(
        database,
        project,
        suffix="isolated",
        home_worker_id=worker.id,
        thread=worktree_thread,
        enqueued_at=clock.value + timedelta(milliseconds=1),
    )

    dispatched = coordinator.dispatch_once()

    assert [entry.turn_id for entry in dispatched] == [
        canonical.id,
        isolated.id,
    ]
    assert {
        key
        for entry in dispatched
        for key in entry.claim.resource_keys
        if key.startswith("workspace:")
    } == {
        f"workspace:project:{project.id}:canonical",
        f"workspace:worktree:{worktree_thread.id}",
    }
    for entry in dispatched:
        assert coordinator.release(
            entry.claim,
            reason="test_cleanup",
            released_at=clock.advance(milliseconds=1),
        )
    coordinator.close()


def test_global_capacity_is_shared_across_coordinators(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    first_registry = FakeRegistry()
    second_registry = FakeRegistry()
    first = ExecutionCoordinator(
        database,
        first_registry.start,
        max_concurrent_turns=1,
        worker_id="worker_capacity_first",
        clock=clock,
    )
    second = ExecutionCoordinator(
        database,
        second_registry.start,
        max_concurrent_turns=1,
        worker_id="worker_capacity_second",
        clock=clock,
    )
    first_worker = first.start(background=False)
    second_worker = second.start(background=False)
    first_project = _add_project(database, tmp_path, "capacity-first")
    second_project = _add_project(database, tmp_path, "capacity-second")
    _, first_turn = _add_turn(
        database,
        first_project,
        suffix="capacity-first",
        home_worker_id=first_worker.id,
    )
    _, second_turn = _add_turn(
        database,
        second_project,
        suffix="capacity-second",
        home_worker_id=second_worker.id,
    )

    first_dispatch = first.dispatch_once()
    assert [entry.turn_id for entry in first_dispatch] == [first_turn.id]
    assert second.dispatch_once() == ()
    assert second_registry.dispatches == []

    assert first.release(
        first_dispatch[0].claim,
        reason="capacity_available",
        released_at=clock.advance(seconds=1),
    )
    second_dispatch = second.dispatch_once()
    assert [entry.turn_id for entry in second_dispatch] == [second_turn.id]
    assert second.release(
        second_dispatch[0].claim,
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    first.close()
    second.close()


def test_fifo_and_home_worker_are_enforced_before_registry_start(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    owner_registry = FakeRegistry()
    foreign_registry = FakeRegistry()
    owner = ExecutionCoordinator(
        database,
        owner_registry.start,
        max_concurrent_turns=2,
        worker_id="worker_fifo_owner",
        clock=clock,
    )
    foreign = ExecutionCoordinator(
        database,
        foreign_registry.start,
        max_concurrent_turns=2,
        worker_id="worker_fifo_foreign",
        clock=clock,
    )
    owner_worker = owner.start(background=False)
    foreign.start(background=False)
    project = _add_project(database, tmp_path, "fifo")
    thread, first = _add_turn(
        database,
        project,
        suffix="fifo-first",
        home_worker_id=owner_worker.id,
        ordinal=1,
        enqueued_at=clock.value,
    )
    _, second = _add_turn(
        database,
        project,
        suffix="fifo-second",
        home_worker_id=owner_worker.id,
        ordinal=2,
        thread=thread,
        enqueued_at=clock.value + timedelta(milliseconds=1),
    )

    assert foreign.dispatch_once() == ()
    first_dispatch = owner.dispatch_once()
    assert [entry.turn_id for entry in first_dispatch] == [first.id]
    _finish_turn(
        database,
        owner,
        first_dispatch[0],
        completed_at=clock.advance(seconds=1),
    )
    second_dispatch = owner.dispatch_once()
    assert [entry.turn_id for entry in second_dispatch] == [second.id]
    assert owner.release(
        second_dispatch[0].claim,
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    owner.close()
    foreign.close()


def test_fifo_does_not_hide_task_class_priority(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FakeRegistry()
    coordinator = ExecutionCoordinator(
        database,
        registry.start,
        max_concurrent_turns=2,
        worker_id="worker_class_fifo",
        clock=clock,
    )
    worker = coordinator.start(background=False)
    first_project = _add_project(database, tmp_path, "scheduled-first")
    second_project = _add_project(database, tmp_path, "interactive-second")
    _, first = _add_turn(
        database,
        first_project,
        suffix="scheduled-first",
        home_worker_id=worker.id,
        execution_class=ExecutionClass.SCHEDULED_AUTOMATION,
        enqueued_at=clock.value,
    )
    _, second = _add_turn(
        database,
        second_project,
        suffix="interactive-second",
        home_worker_id=worker.id,
        execution_class=ExecutionClass.INTERACTIVE,
        enqueued_at=clock.value + timedelta(milliseconds=1),
    )

    dispatched = coordinator.dispatch_once()

    assert [entry.turn_id for entry in dispatched] == [first.id, second.id]
    for entry in dispatched:
        assert coordinator.release(
            entry.claim,
            reason="test_cleanup",
            released_at=clock.advance(milliseconds=1),
        )
    coordinator.close()


def test_stale_release_cannot_clear_rehomed_successor_fence(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    first_registry = FakeRegistry()
    second_registry = FakeRegistry()
    first = ExecutionCoordinator(
        database,
        first_registry.start,
        worker_id="worker_fence_first",
        clock=clock,
    )
    second = ExecutionCoordinator(
        database,
        second_registry.start,
        worker_id="worker_fence_second",
        clock=clock,
    )
    first_worker = first.start(background=False)
    second_worker = second.start(background=False)
    project = _add_project(database, tmp_path, "fence")
    _, turn = _add_turn(
        database,
        project,
        suffix="fence",
        home_worker_id=first_worker.id,
    )

    original = first.dispatch_once()[0]
    assert first.release(
        original.claim,
        reason="rehome",
        released_at=clock.advance(seconds=1),
    )
    assert first.rehome_queued(
        first_worker.id,
        to_worker_id=second_worker.id,
    ) == (turn.id,)
    successor = second.dispatch_once()[0]
    assert successor.claim.turn_epoch == original.claim.turn_epoch + 1
    assert not first.release(
        original.claim,
        reason="stale",
        released_at=clock.advance(seconds=1),
    )
    assert second.claim_is_current(successor.claim)
    assert second.release(
        successor.claim,
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    first.close()
    second.close()


def test_registry_start_failure_keeps_fence_instead_of_retrying_forever(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FailingRegistry()
    coordinator = ExecutionCoordinator(
        database,
        registry.start,
        worker_id="worker_start_failure",
        clock=clock,
    )
    worker = coordinator.start(background=False)
    project = _add_project(database, tmp_path, "start-failure")
    _, turn = _add_turn(
        database,
        project,
        suffix="start-failure",
        home_worker_id=worker.id,
    )

    assert coordinator.dispatch_once() == ()
    assert registry.attempts == 1
    assert len(coordinator.active_claims) == 1
    assert coordinator.active_claims[0].turn_id == turn.id
    assert coordinator.dispatch_once() == ()
    assert registry.attempts == 1

    assert coordinator.release(
        coordinator.active_claims[0],
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    coordinator.close()


def test_dead_worker_recovery_requires_os_proof_and_is_selective(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    clock = MutableClock(utc_now())
    registry = FakeRegistry()
    survivor = ExecutionCoordinator(
        database,
        registry.start,
        max_concurrent_turns=2,
        worker_id="worker_survivor",
        clock=clock,
    )
    survivor_worker = survivor.start(background=False)
    dead = RuntimeWorker(
        id="worker_recovery_target",
        pid=4242,
        surface="test",
        started_at=clock.value,
        heartbeat_at=clock.value,
    )
    live_lock = FileLease.acquire(
        survivor.worker_lock_path(dead.id),
        shared=False,
        blocking=False,
    )
    assert live_lock is not None
    queued_project = _add_project(database, tmp_path, "dead-queued")
    running_project = _add_project(database, tmp_path, "dead-running")
    with database.transaction() as connection:
        RuntimeCoordinationRepository(connection).register_worker(dead)
    queued_thread, queued = _add_turn(
        database,
        queued_project,
        suffix="dead-queued",
        home_worker_id=dead.id,
    )
    running_thread, running = _add_turn(
        database,
        running_project,
        suffix="dead-running",
        home_worker_id=dead.id,
    )
    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        queued_claim = coordination.claim_turn_resources(
            dead.id,
            queued.id,
            (
                "capacity:turn:0",
                f"thread:{queued_thread.id}",
                f"workspace:project:{queued_project.id}:canonical",
            ),
            acquired_at=clock.value,
        )
        running_claim = coordination.claim_turn_resources(
            dead.id,
            running.id,
            (
                "capacity:turn:1",
                f"thread:{running_thread.id}",
                f"workspace:project:{running_project.id}:canonical",
            ),
            acquired_at=clock.value,
        )
        assert queued_claim is not None
        assert running_claim is not None
        turns = TurnRepository(connection)
        current_running = turns.get(running.id)
        assert current_running is not None
        turns.update(
            replace(
                current_running,
                status=TurnStatus.RUNNING,
                started_at=clock.value,
            )
        )

    assert survivor.recover_dead_worker(dead.id) is None
    with database.read() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert coordination.claim_is_current(queued_claim)
        assert coordination.claim_is_current(running_claim)

    live_lock.close()
    recovery = survivor.recover_dead_worker(dead.id)
    assert recovery is not None
    assert recovery.released_queued_turn_ids == (queued.id,)
    assert recovery.rehomed_turn_ids == (queued.id,)
    assert [orphan.turn_id for orphan in recovery.requires_settlement] == [running.id]
    assert recovery.requires_settlement[0].status == "running"
    with database.read() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert not coordination.claim_is_current(queued_claim)
        assert coordination.claim_is_current(running_claim)
        rehomed = TurnRepository(connection).get(queued.id)
        persisted_dead = coordination.get_worker(dead.id)
    assert rehomed is not None
    assert rehomed.home_worker_id == survivor_worker.id
    assert rehomed.execution_owner_id is None
    assert persisted_dead is not None
    assert persisted_dead.stopped_at is not None

    queued_dispatch = survivor.dispatch_once()
    assert [entry.turn_id for entry in queued_dispatch] == [queued.id]
    assert survivor.release(
        queued_dispatch[0].claim,
        reason="test_cleanup",
        released_at=clock.advance(seconds=1),
    )
    survivor.close()
