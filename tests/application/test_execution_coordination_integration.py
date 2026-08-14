from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ConflictError
from core.application.execution_coordinator import ExecutionDispatch
from core.domain import (
    ExecutionClass,
    Project,
    RuntimeWorker,
    Thread,
    ThreadMode,
    TrustState,
    Turn,
)
from core.domain.common import utc_now
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.file_lock import FileLease
from core.persistence import (
    ProjectRepository,
    RuntimeCoordinationRepository,
    ThreadRepository,
    TurnRepository,
)


class _ImmediateSession:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, operation):
        self.history.append({"role": "user", "content": operation.text})
        yield Event("1", TurnStarted())
        yield Event("2", AgentMessage("done"))
        yield Event("3", TaskComplete("done", "completed"))
        self.history.append({"role": "assistant", "content": "done"})

    async def aclose(self) -> None:
        return None


class _ImmediateFactory:
    def create(self, *, workspace, model, approval_callback):
        return _ImmediateSession()


class _HangingSession(_ImmediateSession):
    async def run_stream(self, operation):
        self.history.append({"role": "user", "content": operation.text})
        yield Event("1", TurnStarted())
        await asyncio.Event().wait()


class _HangingFactory:
    def create(self, *, workspace, model, approval_callback):
        return _HangingSession()


def _wait_for(
    application: DeepCodeApplication,
    turn_id: str,
    status: TurnStatus,
) -> Turn:
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        turn = application.turns.read(turn_id).turn
        if turn.status is status:
            return turn
        time.sleep(0.01)
    raise AssertionError(
        f"Turn did not reach {status.value}: {application.turns.read(turn_id).turn}"
    )


def _trusted_thread(
    application: DeepCodeApplication,
    tmp_path: Path,
    suffix: str,
) -> Thread:
    workspace = tmp_path / f"workspace-{suffix}"
    workspace.mkdir()
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    return application.threads.start(project.id, title=suffix)


def test_product_turn_is_bound_claimed_and_released_by_submitting_worker(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_ImmediateFactory(),
        host_surface="desktop",
    )
    thread = _trusted_thread(application, tmp_path, "owned")
    try:
        submitted = application.turns.start(thread.id, prompt="Run once")
        assert (
            submitted.turn.home_worker_id == application.execution_coordinator.worker_id
        )
        assert application.execution_coordinator.worker is not None
        assert application.execution_coordinator.worker.surface == "desktop"
        assert submitted.turn.execution_class is ExecutionClass.INTERACTIVE

        completed = _wait_for(
            application,
            submitted.turn.id,
            TurnStatus.COMPLETED,
        )
        assert completed.execution_owner_id is None
        assert completed.home_worker_id is None
        assert completed.execution_epoch == 1
        assert application.execution_coordinator.active_claims == ()
        with application.database.read() as connection:
            held = RuntimeCoordinationRepository(
                connection
            ).list_held_resources_for_worker(
                application.execution_coordinator.worker_id
            )
        assert held == []
    finally:
        application.close()


def test_claimed_turn_must_still_be_queued_and_hold_its_fence(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_HangingFactory(),
    )
    application.execution_coordinator.quiesce()
    thread = _trusted_thread(application, tmp_path, "fenced")
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Claim me",
        home_worker_id=application.execution_coordinator.worker_id,
    )
    with application.database.transaction() as connection:
        TurnRepository(connection).add(turn)
        claim = RuntimeCoordinationRepository(connection).claim_turn_resources(
            application.execution_coordinator.worker_id,
            turn.id,
            (
                "capacity:turn:0",
                f"thread:{thread.id}",
                f"workspace:project:{thread.project_id}:canonical",
            ),
            acquired_at=turn.enqueued_at,
        )
    assert claim is not None
    dispatch = ExecutionDispatch(
        turn_id=turn.id,
        thread_id=thread.id,
        project_id=thread.project_id,
        executor=turn.executor,
        execution_class=turn.execution_class,
        claim=claim,
    )
    try:
        running, _, _ = application.turns._mark_running(
            turn.id,
            claim=dispatch.claim,
        )
        assert running.status is TurnStatus.RUNNING
        with pytest.raises(ConflictError, match="queued"):
            application.turns._mark_running(
                turn.id,
                claim=dispatch.claim,
            )
        application.turns._finish_unstarted(
            turn.id,
            status=TurnStatus.INTERRUPTED,
            stop_reason="test_cleanup",
            claim=dispatch.claim,
        )
    finally:
        application.close()


def test_registry_start_failure_atomically_fails_turn_and_releases_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_ImmediateFactory(),
    )
    thread = _trusted_thread(application, tmp_path, "start-failure")

    def reject_start(*_args, **_kwargs) -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(application.executions, "start", reject_start)
    try:
        submitted = application.turns.start(thread.id, prompt="Fail admission")
        failed = _wait_for(application, submitted.turn.id, TurnStatus.FAILED)
        assert failed.stop_reason == "scheduler_error"
        assert failed.error_code == "SCHEDULER_ERROR"
        assert failed.execution_owner_id is None
        assert application.execution_coordinator.active_claims == ()
        snapshot = application.turns.read(failed.id)
        assert snapshot.items[-1].payload["stopReason"] == "scheduler_error"
        with application.database.read() as connection:
            held = RuntimeCoordinationRepository(
                connection
            ).list_held_resources_for_worker(
                application.execution_coordinator.worker_id
            )
        assert held == []
    finally:
        application.close()


def test_dead_running_turn_is_interrupted_and_never_replayed(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_ImmediateFactory(),
    )
    application.execution_coordinator.quiesce()
    workspace = tmp_path / "workspace-orphan"
    workspace.mkdir()
    project = Project(
        canonical_path=str(workspace),
        display_name="Orphan",
        trust_state=TrustState.TRUSTED,
    )
    thread = Thread(
        project_id=project.id,
        title="Orphan",
        mode=ThreadMode.CODE,
        workspace_path=str(workspace),
    )
    dead = RuntimeWorker(
        id="worker_dead_integration",
        pid=4242,
        surface="test",
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Must not replay",
        home_worker_id=dead.id,
    )
    with application.database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(dead)
        TurnRepository(connection).add(turn)
        claim = coordination.claim_turn_resources(
            dead.id,
            turn.id,
            (
                "capacity:turn:0",
                f"thread:{thread.id}",
                f"workspace:project:{project.id}:canonical",
            ),
            acquired_at=turn.enqueued_at,
        )
        assert claim is not None
        turns = TurnRepository(connection)
        claimed = turns.get(turn.id)
        assert claimed is not None
        turns.update(
            replace(
                claimed,
                status=TurnStatus.RUNNING,
                started_at=turn.enqueued_at,
            )
        )

    try:
        recovered = application.execution_coordinator.recover_dead_worker(dead.id)
        assert recovered is not None
        interrupted = application.turns.read(turn.id).turn
        assert interrupted.status is TurnStatus.INTERRUPTED
        assert interrupted.stop_reason == "worker_crashed"
        assert interrupted.execution_owner_id is None
        assert (
            application.turns.read(turn.id).items[-1].payload["stopReason"]
            == "worker_crashed"
        )
        with application.database.read() as connection:
            assert not RuntimeCoordinationRepository(connection).claim_is_current(claim)
    finally:
        application.close()


def test_dead_workers_unclaimed_queued_turn_is_safely_rehomed(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_ImmediateFactory(),
    )
    application.execution_coordinator.quiesce()
    workspace = tmp_path / "workspace-rehome"
    workspace.mkdir()
    project = Project(
        canonical_path=str(workspace),
        display_name="Rehome",
        trust_state=TrustState.TRUSTED,
    )
    thread = Thread(
        project_id=project.id,
        title="Rehome",
        mode=ThreadMode.CODE,
        workspace_path=str(workspace),
    )
    dead = RuntimeWorker(
        id="worker_dead_queued",
        pid=4243,
        surface="test",
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Safe queued work",
        home_worker_id=dead.id,
    )
    with application.database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        RuntimeCoordinationRepository(connection).register_worker(dead)
        TurnRepository(connection).add(turn)

    try:
        recoveries = application.execution_coordinator.recover_candidates(
            heartbeat_before=utc_now(),
        )
        assert len(recoveries) == 1
        assert recoveries[0].rehomed_turn_ids == (turn.id,)
        rehomed = application.turns.read(turn.id).turn
        assert rehomed.home_worker_id == application.execution_coordinator.worker_id
        assert rehomed.execution_owner_id is None
    finally:
        application.close()


def test_second_application_requests_cancel_from_claim_owner(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    first = DeepCodeApplication.open(
        database_path,
        session_factory=_HangingFactory(),
    )
    thread = _trusted_thread(first, tmp_path, "remote-cancel")
    submitted = first.turns.start(thread.id, prompt="Keep running")
    _wait_for(first, submitted.turn.id, TurnStatus.RUNNING)
    second = DeepCodeApplication.open(
        database_path,
        session_factory=_HangingFactory(),
        session_store=first.session_store,
    )
    try:
        accepted, interrupted = second.turns.interrupt(
            thread.id,
            submitted.turn.id,
            timeout=4.0,
        )
        assert accepted is True
        assert interrupted.status is TurnStatus.INTERRUPTED
        assert interrupted.cancel_requested_at is not None
        assert interrupted.execution_owner_id is None
        assert first.execution_coordinator.active_claims == ()
    finally:
        second.close()
        first.close()


def test_close_keeps_worker_liveness_lock_when_claim_did_not_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_HangingFactory(),
    )
    thread = _trusted_thread(application, tmp_path, "close-fence")
    submitted = application.turns.start(thread.id, prompt="Keep claim")
    _wait_for(application, submitted.turn.id, TurnStatus.RUNNING)
    original_close = application.executions.close
    monkeypatch.setattr(application.executions, "close", lambda **_kwargs: None)

    with pytest.raises(RuntimeError, match="execution claims remain"):
        application.close()
    competing = FileLease.acquire(
        application.execution_coordinator.worker_lock_path(
            application.execution_coordinator.worker_id
        ),
        shared=False,
        blocking=False,
    )
    assert competing is None

    monkeypatch.setattr(application.executions, "close", original_close)
    application.executions.interrupt(submitted.turn.id)
    _wait_for(application, submitted.turn.id, TurnStatus.INTERRUPTED)
    application.close()
