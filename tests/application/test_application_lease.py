from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from core.application import DeepCodeApplication
from core.application.application_lease import ApplicationLease
from core.application.errors import UpgradeRequiresExclusiveAccessError
from core.domain.project import TrustState
from core.domain.turn import TurnStatus
from core.events import Event, TurnStarted
from core.persistence.database import Database
from core.persistence.migrations import LATEST_SCHEMA_VERSION, current_version


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


def _hold_exclusive_application_lease(database_path: str, ready) -> None:
    lease = ApplicationLease.acquire(Path(database_path))
    ready.put(lease.recovery_owner)
    try:
        while True:
            time.sleep(1)
    finally:
        lease.close()


def _report_application_lease(database_path: str, result) -> None:
    lease = ApplicationLease.acquire(Path(database_path))
    try:
        result.put(lease.recovery_owner)
        lease.downgrade()
    finally:
        lease.close()


def _stress_application_lease(
    database_path: str,
    barrier,
    results,
    active_recoveries,
    peak_recoveries,
    counter_lock,
    iterations: int,
) -> None:
    for iteration in range(iterations):
        barrier.wait(timeout=10)
        lease = ApplicationLease.acquire(Path(database_path))
        try:
            if lease.recovery_owner:
                with counter_lock:
                    active_recoveries.value += 1
                    peak_recoveries.value = max(
                        peak_recoveries.value,
                        active_recoveries.value,
                    )
                time.sleep(0.005)
                with counter_lock:
                    active_recoveries.value -= 1
            lease.downgrade()
            results.put((iteration, lease.recovery_owner))
            # No process releases its shared lifetime lease until every
            # contender has completed startup classification.
            barrier.wait(timeout=10)
        finally:
            lease.close()
        barrier.wait(timeout=10)


def _hold_legacy_schema(
    database_path: str,
    ready,
    release,
    target_version: int,
) -> None:
    database = Database(database_path)
    database.initialize(target_version=target_version)
    lease = ApplicationLease.acquire(database.path)
    try:
        assert lease.recovery_owner
        lease.downgrade()
        ready.put(database.schema_version())
        release.wait(timeout=10)
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


def test_waiting_process_becomes_recovery_owner_if_startup_owner_crashes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    result = context.Queue()
    owner = context.Process(
        target=_hold_exclusive_application_lease,
        args=(str(database_path), ready),
    )
    successor = context.Process(
        target=_report_application_lease,
        args=(str(database_path), result),
    )
    owner.start()
    try:
        assert ready.get(timeout=5) is True
        successor.start()
        time.sleep(0.05)
        owner.terminate()
        owner.join(timeout=5)
        assert not owner.is_alive()
        assert result.get(timeout=5) is True
        successor.join(timeout=5)
        assert successor.exitcode == 0
    finally:
        if owner.is_alive():
            owner.kill()
            owner.join(timeout=2)
        if successor.is_alive():
            successor.kill()
            successor.join(timeout=2)


def test_three_process_startup_stress_never_overlaps_recovery(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    context = multiprocessing.get_context("spawn")
    process_count = 3
    iterations = 12
    barrier = context.Barrier(process_count)
    results = context.Queue()
    active_recoveries = context.Value("i", 0)
    peak_recoveries = context.Value("i", 0)
    counter_lock = context.Lock()
    processes = [
        context.Process(
            target=_stress_application_lease,
            args=(
                str(database_path),
                barrier,
                results,
                active_recoveries,
                peak_recoveries,
                counter_lock,
                iterations,
            ),
        )
        for _ in range(process_count)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=20)
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        owners_by_iteration: dict[int, int] = defaultdict(int)
        for _ in range(process_count * iterations):
            iteration, recovery_owner = results.get(timeout=2)
            owners_by_iteration[iteration] += int(recovery_owner)
        assert owners_by_iteration == {iteration: 1 for iteration in range(iterations)}
        assert peak_recoveries.value == 1
        assert active_recoveries.value == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=2)


def test_live_old_schema_requires_exclusive_upgrade_then_preserves_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    old_version = 1
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    old_process = context.Process(
        target=_hold_legacy_schema,
        args=(str(database_path), ready, release, old_version),
    )
    old_process.start()
    try:
        assert ready.get(timeout=10) == old_version
        with pytest.raises(UpgradeRequiresExclusiveAccessError) as raised:
            DeepCodeApplication.open(database_path)
        assert raised.value.code == "UPGRADE_REQUIRES_EXCLUSIVE_ACCESS"
        assert raised.value.retryable is True
        assert raised.value.details == {
            "installedSchemaVersion": old_version,
            "requiredSchemaVersion": LATEST_SCHEMA_VERSION,
        }
        with sqlite3.connect(database_path) as connection:
            assert current_version(connection) == old_version
        assert not (tmp_path / "backups").exists()
    finally:
        release.set()
        old_process.join(timeout=10)
        if old_process.is_alive():
            old_process.kill()
            old_process.join(timeout=2)
    assert old_process.exitcode == 0

    upgraded = DeepCodeApplication.open(database_path)
    try:
        assert upgraded.database.schema_version() == LATEST_SCHEMA_VERSION
    finally:
        upgraded.close()
    backups = list(
        (tmp_path / "backups").glob(
            f"state.pre-v{old_version}-to-v{LATEST_SCHEMA_VERSION}-*.sqlite3"
        )
    )
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert current_version(connection) == old_version
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_current_schema_joiner_never_runs_database_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    owner = DeepCodeApplication.open(database_path)

    def reject_initialize(_database, **_kwargs) -> None:
        raise AssertionError("shared application joiner attempted initialization")

    monkeypatch.setattr(Database, "initialize", reject_initialize)
    joiner: DeepCodeApplication | None = None
    try:
        joiner = DeepCodeApplication.open(database_path)
        assert joiner._application_lease is not None
        assert joiner._application_lease.recovery_owner is False
        assert joiner.database.schema_version() == LATEST_SCHEMA_VERSION
    finally:
        if joiner is not None:
            joiner.close()
        owner.close()


def test_application_close_aggregates_failures_and_retains_lifetime_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    relay_close = application.event_relay.close
    scheduler_close = application.automation_scheduler.close
    calls: list[str] = []

    def fail_relay() -> None:
        calls.append("relay")
        raise RuntimeError("relay shutdown failed")

    def fail_scheduler() -> None:
        calls.append("scheduler")
        raise RuntimeError("scheduler shutdown failed")

    monkeypatch.setattr(application.event_relay, "close", fail_relay)
    monkeypatch.setattr(application.automation_scheduler, "close", fail_scheduler)
    with pytest.raises(ExceptionGroup) as raised:
        application.close()
    assert calls == ["relay", "scheduler"]
    assert len(raised.value.exceptions) == 2
    assert application.execution_coordinator.worker is None
    assert application._application_lease is not None

    monkeypatch.setattr(application.event_relay, "close", relay_close)
    monkeypatch.setattr(application.automation_scheduler, "close", scheduler_close)
    application.close()
    assert application._application_lease is None


def test_application_close_retains_lease_when_listener_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    remove_guard = application.turns.remove_admission_guard

    def fail_remove_guard(_guard) -> None:
        raise RuntimeError("admission guard cleanup failed")

    monkeypatch.setattr(
        application.turns,
        "remove_admission_guard",
        fail_remove_guard,
    )
    with pytest.raises(RuntimeError, match="admission guard cleanup failed"):
        application.close()
    assert application._application_lease is not None

    monkeypatch.setattr(application.turns, "remove_admission_guard", remove_guard)
    application.close()
    assert application._application_lease is None


def test_application_close_retains_lease_on_execution_shutdown_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    execution_close = application.executions.close
    close_live_sessions = application.turns.close_live_sessions

    application.executions.start(
        "shutdown-timeout",
        lambda: asyncio.sleep(60),
    )

    async def hang_cleanup() -> None:
        await asyncio.Event().wait()

    def close_quickly(*, cleanup=None, **_kwargs) -> None:
        execution_close(timeout=0.02, cleanup=cleanup)

    monkeypatch.setattr(application.turns, "close_live_sessions", hang_cleanup)
    monkeypatch.setattr(application.executions, "close", close_quickly)
    with pytest.raises(
        TimeoutError,
        match="cleanup did not finish before the shutdown timeout",
    ):
        application.close()
    assert application._application_lease is not None
    assert application.executions._closed is True

    monkeypatch.setattr(
        application.turns,
        "close_live_sessions",
        close_live_sessions,
    )
    monkeypatch.setattr(application.executions, "close", execution_close)
    application.close()
    assert application._application_lease is None


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
