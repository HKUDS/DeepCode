"""Drain and activity queries over the existing application execution owners."""

from __future__ import annotations

import threading
import time

from core.application.application import DeepCodeApplication
from core.domain.turn import TurnStatus
from core.persistence.execution_repository import TurnRepository


class ServiceLifecycle:
    def __init__(self, application: DeepCodeApplication) -> None:
        self.application = application
        self._paused = False
        self._scheduler_was_active = False

    def resume(self) -> None:
        """Restore admission after a supervisor could not finish a stop."""
        if self._paused:
            self.application.execution_coordinator.resume_admission()
        if self._scheduler_was_active:
            self.application.automation_scheduler.start()
        self._paused = False
        self._scheduler_was_active = False

    def activity(self) -> dict[str, int | bool]:
        app = self.application
        worker_id = app.execution_coordinator.worker_id
        with app.database.read() as connection:
            turns = TurnRepository(connection).list_active()
        owned = [
            turn
            for turn in turns
            if turn.execution_owner_id == worker_id
            or (turn.execution_owner_id is None and turn.home_worker_id == worker_id)
        ]
        return {
            "activeTurns": sum(turn.status != TurnStatus.QUEUED for turn in owned),
            "queuedTurns": sum(turn.status == TurnStatus.QUEUED for turn in owned),
            "terminals": app.terminals.active_count,
            "schedulerActive": app.automation_scheduler.active,
            "schedulerLeader": app.automation_scheduler.leader,
        }

    def drain(self, timeout: float, interrupt: threading.Event) -> bool:
        """Wait for admitted work, restoring admission if the wait does not finish.

        Queued work stays durable and unstarted. Normal application shutdown
        settles it using the existing worker/Goal recovery semantics.
        """
        app = self.application
        if self._paused:
            return True
        self._scheduler_was_active = app.automation_scheduler.active
        app.automation_scheduler.close()
        drained = False
        try:
            app.execution_coordinator.pause_admission()
            self._paused = True
            deadline = time.monotonic() + timeout
            while not interrupt.is_set():
                if (
                    not app.execution_coordinator.active_claims
                    and not app.terminals.active_count
                ):
                    drained = True
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                interrupt.wait(min(0.05, remaining))
            return False
        finally:
            if not drained:
                self.resume()
