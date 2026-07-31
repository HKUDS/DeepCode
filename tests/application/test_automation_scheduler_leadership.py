from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from pathlib import Path

from core.application import DeepCodeApplication
from core.application.automation_scheduler import automation_scheduler_lease_path
from core.file_lock import FileLease
from core.sessions import SessionStore


def _wait_until(
    predicate: Callable[[], bool],
    message: str,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


def _leader_count(*applications: DeepCodeApplication) -> int:
    return sum(application.automation_scheduler.leader for application in applications)


def _hold_scheduler_lease(path: str, ready) -> None:
    lease = FileLease.acquire(Path(path), shared=False, blocking=True)
    assert lease is not None
    ready.put(True)
    try:
        while True:
            time.sleep(1)
    finally:
        lease.close()


def test_application_scheduler_is_opt_in(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions"),
        host_surface="management_cli",
    )
    try:
        assert not application.automation_scheduler.active
        assert not application.automation_scheduler.leader
        assert not application.automations.scheduler_active
        assert application.execution_coordinator.worker is not None
        assert application.execution_coordinator.worker.surface == "management_cli"
    finally:
        application.close()


def test_one_scheduler_leader_and_live_follower_takeover(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    session_store = SessionStore(tmp_path / "sessions")
    first = DeepCodeApplication.open(
        database_path,
        session_store=session_store,
        run_automation_scheduler=True,
    )
    second = DeepCodeApplication.open(
        database_path,
        session_store=session_store,
        run_automation_scheduler=True,
    )
    first_closed = False
    second_closed = False
    try:
        _wait_until(
            lambda: _leader_count(first, second) == 1,
            "exactly one Automation scheduler did not become leader",
        )
        assert first.automation_scheduler.active
        assert second.automation_scheduler.active
        assert first.automations.scheduler_active
        assert second.automations.scheduler_active

        leader, follower = (
            (first, second) if first.automation_scheduler.leader else (second, first)
        )
        leader.close()
        assert not leader.automation_scheduler.active
        assert not leader.automation_scheduler.leader
        if leader is first:
            first_closed = True
        else:
            second_closed = True

        _wait_until(
            lambda: follower.automation_scheduler.leader,
            "live scheduler follower did not take over",
        )
    finally:
        if not first_closed:
            first.close()
        if not second_closed:
            second.close()


def test_scheduler_leadership_recovers_after_owner_process_is_killed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    lock_path = automation_scheduler_lease_path(database_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    owner = context.Process(
        target=_hold_scheduler_lease,
        args=(str(lock_path), ready),
    )
    owner.start()
    application: DeepCodeApplication | None = None
    try:
        assert ready.get(timeout=5) is True
        application = DeepCodeApplication.open(
            database_path,
            session_store=SessionStore(tmp_path / "sessions"),
            run_automation_scheduler=True,
        )
        time.sleep(0.1)
        assert not application.automation_scheduler.leader

        owner.terminate()
        owner.join(timeout=5)
        assert not owner.is_alive()
        _wait_until(
            lambda: application is not None and application.automation_scheduler.leader,
            "scheduler did not take over after the owner process exited",
        )
    finally:
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=5)
        if application is not None:
            application.close()
