"""Process-local Automation scheduling with cross-process leadership."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from core.domain.automation import AutomationRun
from core.domain.common import utc_now
from core.file_lock import FileLease


logger = logging.getLogger(__name__)
SCHEDULER_STANDBY_POLL_SECONDS = 0.5
SCHEDULER_LEADER_POLL_CEILING_SECONDS = 5.0


def automation_scheduler_lease_path(database_path: Path) -> Path:
    """Return the single canonical leader-lease path for a database."""

    return database_path.with_name(f"{database_path.name}.automation-scheduler.lock")


class AutomationSchedulerPort(Protocol):
    """Narrow scheduling surface consumed by AutomationService."""

    @property
    def active(self) -> bool:
        """Whether this process has a live scheduler worker."""

    def wake(self) -> None:
        """Wake the worker after schedule state changes."""


class AutomationScheduler:
    """Run due Automations from the single process holding the leader lease."""

    def __init__(
        self,
        database_path: Path,
        *,
        run_due: Callable[[], tuple[AutomationRun, ...]],
        next_due: Callable[[], datetime | None],
    ) -> None:
        self._lease_path = automation_scheduler_lease_path(database_path)
        self._run_due = run_due
        self._next_due = next_due
        self._state_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease: FileLease | None = None

    @property
    def active(self) -> bool:
        with self._state_lock:
            return bool(
                self._thread is not None
                and self._thread.is_alive()
                and not self._stop.is_set()
            )

    @property
    def leader(self) -> bool:
        """Whether this process currently owns scheduled-trigger leadership."""

        with self._state_lock:
            return self._lease is not None

    @property
    def lease_path(self) -> Path:
        return self._lease_path

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deepcode-automation-scheduler",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def close(self) -> None:
        """Stop the worker and wait for its lease-releasing ``finally`` block."""

        self._stop.set()
        self._wake.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15.0)
            if thread.is_alive():
                raise RuntimeError("automation scheduler did not stop cleanly")
        with self._state_lock:
            if self._thread is thread:
                self._thread = None

    def _run(self) -> None:
        lease: FileLease | None = None
        try:
            while not self._stop.is_set():
                if lease is None:
                    lease = FileLease.acquire(
                        self._lease_path,
                        shared=False,
                        blocking=False,
                    )
                    if lease is None:
                        self._wait(SCHEDULER_STANDBY_POLL_SECONDS)
                        continue
                    with self._state_lock:
                        self._lease = lease
                timeout = SCHEDULER_LEADER_POLL_CEILING_SECONDS
                try:
                    self._run_due()
                    timeout = self._next_timeout()
                except Exception:  # noqa: BLE001 - leader remains available
                    logger.exception("automation scheduler pass failed")
                self._wait(timeout)
        finally:
            with self._state_lock:
                if self._lease is lease:
                    self._lease = None
            if lease is not None:
                lease.close()

    def _wait(self, timeout: float) -> None:
        self._wake.wait(timeout)
        self._wake.clear()

    def _next_timeout(self) -> float:
        due_at = self._next_due()
        if due_at is None:
            return SCHEDULER_LEADER_POLL_CEILING_SECONDS
        return max(
            0.05,
            min(
                SCHEDULER_LEADER_POLL_CEILING_SECONDS,
                (due_at - utc_now()).total_seconds(),
            ),
        )


__all__ = [
    "AutomationScheduler",
    "AutomationSchedulerPort",
    "automation_scheduler_lease_path",
]
