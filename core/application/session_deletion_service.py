"""Permanent Session deletion shared by every DeepCode frontend."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.application.errors import (
    ApplicationError,
    ConflictError,
    ThreadNotFoundError,
)
from core.domain.goal import GoalStatus
from core.persistence.automation_repository import AutomationRepository
from core.persistence.database import Database
from core.persistence.execution_repository import TurnRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import WorkflowRepository
from core.sessions import GoalStore, SessionStore
from core.sessions.deletion import SessionDeletionTicket
from core.sessions.store import SessionDeletionGuard


logger = logging.getLogger(__name__)
DeletionCallback = Callable[[str], None]
ProjectionCallback = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class SessionDeletionResult:
    thread_id: str
    cleanup_pending: bool = False


class SessionDeletionService:
    """Validate, quarantine, and cascade one canonical Session deletion.

    The Session JSONL directory disappears from discovery before the SQLite
    projection is removed.  A filesystem tombstone bridges those two stores;
    if the process stops in between, :meth:`recover_pending` deterministically
    finishes the same deletion instead of resurrecting history.
    """

    def __init__(
        self,
        database: Database,
        sessions: SessionStore,
        goals: GoalStore,
        *,
        ensure_projection: ProjectionCallback | None = None,
        on_deleted: DeletionCallback | None = None,
    ) -> None:
        self.database = database
        self.sessions = sessions
        self.goals = goals
        self._ensure_projection = ensure_projection
        self._on_deleted = on_deleted

    def delete(self, thread_id: str) -> SessionDeletionResult:
        activity = self.sessions.acquire_deletion_lease(thread_id)
        if activity is None:
            raise ConflictError(
                "Session is open in another DeepCode surface",
                user_message=(
                    "Close the CLI, terminal, or worktree operation using this "
                    "Session, then try again."
                ),
                details={
                    "threadId": thread_id,
                    "blockers": [
                        {
                            "code": "SESSION_IN_USE",
                            "message": "Session has a live cross-process owner.",
                        }
                    ],
                },
            )
        with activity:
            self._project_canonical_session(thread_id)
            with self.sessions.deletion_guard(thread_id) as guarded:
                result = self._delete_guarded(thread_id, guarded)
        self._notify_deleted(thread_id)
        return result

    def recover_pending(self) -> int:
        """Finish committed filesystem tombstones left by a stopped process."""

        recovered = 0
        for ticket in self.sessions.pending_deletions():
            activity = self.sessions.acquire_deletion_lease(ticket.session_id)
            if activity is None:
                continue
            try:
                with (
                    activity,
                    self.sessions.deletion_guard(ticket.session_id) as guarded,
                ):
                    self._recover_guarded(ticket, guarded)
                self._notify_deleted(ticket.session_id)
                recovered += 1
            except Exception:  # noqa: BLE001 - isolate damaged tombstones
                logger.exception(
                    "Session deletion recovery failed for %s",
                    ticket.session_id,
                )
        return recovered

    def _recover_guarded(
        self,
        ticket: SessionDeletionTicket | None,
        guarded: SessionDeletionGuard,
    ) -> SessionDeletionResult:
        if ticket is None:
            raise ApplicationError(
                f"Session deletion tombstone is corrupt: {guarded.session_id}",
                user_message=(
                    "This Session has an incomplete deletion record. Run "
                    "DeepCode diagnostics before modifying it."
                ),
            )
        guarded.ensure_quarantined(ticket)
        with self.database.transaction() as connection:
            ThreadRepository(connection).remove(ticket.session_id)
        cleanup_pending = not guarded.finalize(ticket)
        return SessionDeletionResult(
            ticket.session_id,
            cleanup_pending=cleanup_pending,
        )

    def _delete_guarded(
        self,
        thread_id: str,
        guarded: SessionDeletionGuard,
    ) -> SessionDeletionResult:
        if guarded.pending:
            return self._recover_guarded(guarded.pending_ticket, guarded)
        if not guarded.exists:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")

        ticket: SessionDeletionTicket | None = None
        try:
            with self.database.transaction() as connection:
                thread = ThreadRepository(connection).get(thread_id)
                if thread is None:
                    raise ThreadNotFoundError(f"thread not found: {thread_id}")
                blockers = self._blockers(connection, thread_id, guarded.directory)
                if blockers:
                    raise ConflictError(
                        "Session cannot be deleted while related work is active",
                        user_message=str(blockers[0]["message"]),
                        details={"threadId": thread_id, "blockers": blockers},
                    )
                ticket = guarded.stage()
                if not ThreadRepository(connection).remove(thread_id):
                    raise ThreadNotFoundError(f"thread not found: {thread_id}")
        except BaseException:
            if ticket is not None:
                guarded.rollback(ticket)
            raise

        return SessionDeletionResult(
            thread_id,
            cleanup_pending=not guarded.finalize(ticket),
        )

    def _project_canonical_session(self, thread_id: str) -> None:
        if self.sessions.is_deletion_pending(thread_id):
            return
        with self.database.read() as connection:
            projected = ThreadRepository(connection).get(thread_id)
        if projected is None and self.sessions.get_session(thread_id) is not None:
            if self._ensure_projection is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            self._ensure_projection(thread_id)

    def _blockers(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        directory: Path,
    ) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        active_turn = TurnRepository(connection).active_for_thread(thread_id)
        if active_turn is not None:
            blockers.append(
                {
                    "code": "ACTIVE_TURN",
                    "message": "Stop the active or queued Turn before deleting this Session.",
                }
            )
        active_workflow = WorkflowRepository(connection).active_for_thread(thread_id)
        if active_workflow is not None:
            blockers.append(
                {
                    "code": "ACTIVE_WORKFLOW",
                    "message": "Stop the active workflow before deleting this Session.",
                }
            )
        automation = AutomationRepository(connection).get_for_thread(thread_id)
        if automation is not None:
            blockers.append(
                {
                    "code": "AUTOMATION_ATTACHED",
                    "message": (
                        "Remove the Automation attached to this Session before deleting it."
                    ),
                }
            )
        thread = ThreadRepository(connection).get(thread_id)
        if thread is not None and thread.worktree_path is not None:
            blockers.append(
                {
                    "code": "WORKTREE_ATTACHED",
                    "message": (
                        "Clean or detach the managed worktree before deleting this Session."
                    ),
                }
            )
        goal = self.goals.read_guarded(thread_id, directory)
        if goal is not None and goal.goal.status is GoalStatus.ACTIVE:
            blockers.append(
                {
                    "code": "ACTIVE_GOAL",
                    "message": "Pause or finish the active Goal before deleting this Session.",
                }
            )
        return blockers

    def _notify_deleted(self, thread_id: str) -> None:
        if self._on_deleted is None:
            return
        try:
            self._on_deleted(thread_id)
        except Exception:  # noqa: BLE001 - deletion is already durable
            logger.exception("post-delete cleanup failed for %s", thread_id)


__all__ = ["SessionDeletionResult", "SessionDeletionService"]
