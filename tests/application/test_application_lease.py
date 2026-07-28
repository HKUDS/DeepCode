from __future__ import annotations

import asyncio
import multiprocessing
import time
from pathlib import Path
from typing import Any

from core.application import DeepCodeApplication
from core.application.application_lease import ApplicationLease
from core.domain.project import TrustState
from core.domain.turn import TurnStatus
from core.events import Event, TurnStarted


class HangingSession:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("1", TurnStarted())
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        return None


class HangingFactory:
    def create(self, *, workspace, model, approval_callback):
        return HangingSession()


def _hold_application_lease(
    database_path: str,
    ready,
    release,
) -> None:
    lease = ApplicationLease.acquire(Path(database_path))
    try:
        ready.put(lease.recovery_owner)
        lease.downgrade()
        release.wait(timeout=5)
    finally:
        lease.close()


def _wait_for_status(
    application: DeepCodeApplication,
    turn_id: str,
    status: TurnStatus,
) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if application.turns.read(turn_id).turn.status is status:
            return
        time.sleep(0.01)
    raise AssertionError(f"turn {turn_id} did not reach {status.value}")


def test_application_lease_grants_recovery_only_without_live_holders(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    first = ApplicationLease.acquire(database_path)
    assert first.recovery_owner is True
    first.downgrade()

    second = ApplicationLease.acquire(database_path)
    try:
        assert second.recovery_owner is False
    finally:
        second.close()
        first.close()

    third = ApplicationLease.acquire(database_path)
    try:
        assert third.recovery_owner is True
    finally:
        third.close()


def test_application_lease_coordinates_independent_processes(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    process = context.Process(
        target=_hold_application_lease,
        args=(str(database_path), ready, release),
    )
    process.start()
    try:
        assert ready.get(timeout=5) is True
        joined = ApplicationLease.acquire(database_path)
        try:
            assert joined.recovery_owner is False
        finally:
            joined.close()
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
    assert process.exitcode == 0


def test_second_application_does_not_recover_another_process_live_turn(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = DeepCodeApplication.open(
        database_path,
        session_factory=HangingFactory(),
    )
    project = first.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = first.threads.start(project.id, title="Live elsewhere")
    started = first.turns.start(thread.id, prompt="keep running")
    _wait_for_status(first, started.turn.id, TurnStatus.RUNNING)

    second = DeepCodeApplication.open(
        database_path,
        session_factory=HangingFactory(),
    )
    try:
        assert second.turns.read(started.turn.id).turn.status is TurnStatus.RUNNING
    finally:
        second.close()
        first.turns.interrupt(thread.id, started.turn.id)
        _wait_for_status(first, started.turn.id, TurnStatus.INTERRUPTED)
        first.close()
