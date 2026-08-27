from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ConflictError, ThreadNotFoundError
from core.domain import TrustState
from core.domain.automation import AutomationScheduleKind
from core.domain.turn import Turn
from core.persistence.execution_repository import TurnRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore
from core.sessions.deletion import SessionDeletionJournal


def _application(tmp_path: Path) -> tuple[DeepCodeApplication, str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    sessions = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=sessions,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Disposable")
    sessions.append_message(thread.id, "user", "persisted history")
    return application, project.id, thread.id


def _row_count(application: DeepCodeApplication, table: str, thread_id: str) -> int:
    column = "id" if table == "threads" else "thread_id"
    with application.database.read() as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
            (thread_id,),
        ).fetchone()
    return int(row[0])


def test_delete_removes_canonical_session_and_sqlite_projection(tmp_path: Path) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    try:
        assert _row_count(application, "event_log", thread_id) > 0

        result = application.deletions.delete(thread_id)

        assert result.thread_id == thread_id
        assert result.cleanup_pending is False
        assert application.session_store.get_session(thread_id) is None
        assert _row_count(application, "threads", thread_id) == 0
        assert _row_count(application, "event_log", thread_id) == 0
        with pytest.raises(ThreadNotFoundError):
            application.threads.read(thread_id)
    finally:
        application.close()

    reopened = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions"),
    )
    try:
        with pytest.raises(ThreadNotFoundError):
            reopened.threads.read(thread_id)
    finally:
        reopened.close()


def test_delete_projects_a_cli_only_session_created_after_application_start(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=sessions,
    )
    cli_session = sessions.create_session(
        title="Created by a live CLI",
        metadata={"kind": "tui", "workspace": str(workspace)},
    )
    sessions.append_message(cli_session.session_id, "user", "hello")
    try:
        with application.database.read() as connection:
            assert ThreadRepository(connection).get(cli_session.session_id) is None

        result = application.deletions.delete(cli_session.session_id)

        assert result.thread_id == cli_session.session_id
        assert sessions.get_session(cli_session.session_id) is None
        assert _row_count(application, "threads", cli_session.session_id) == 0
    finally:
        application.close()


def test_database_failure_restores_quarantined_session(tmp_path: Path) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    try:
        with patch.object(
            ThreadRepository,
            "remove",
            side_effect=RuntimeError("database write failed"),
        ), pytest.raises(RuntimeError, match="database write failed"):
            application.deletions.delete(thread_id)

        assert application.session_store.get_session(thread_id) is not None
        assert application.session_store.is_deletion_pending(thread_id) is False
        assert _row_count(application, "threads", thread_id) == 1
    finally:
        application.close()


def test_startup_finishes_interrupted_deletion_without_resurrection(
    tmp_path: Path,
) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    with application.session_store.deletion_guard(thread_id) as guarded:
        guarded.stage()
    assert _row_count(application, "threads", thread_id) == 1
    application.close()

    recovered = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions"),
    )
    try:
        assert recovered.session_store.pending_deletions() == ()
        assert _row_count(recovered, "threads", thread_id) == 0
        with pytest.raises(ThreadNotFoundError):
            recovered.threads.read(thread_id)
    finally:
        recovered.close()


def test_live_activity_lease_blocks_deletion(tmp_path: Path) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    activity = application.session_store.acquire_activity_lease(thread_id)
    assert activity is not None
    try:
        with pytest.raises(ConflictError) as raised:
            application.deletions.delete(thread_id)
        assert raised.value.details["blockers"][0]["code"] == "SESSION_IN_USE"
        assert application.session_store.get_session(thread_id) is not None
    finally:
        activity.close()
        application.close()


def test_queued_turn_blocks_deletion(tmp_path: Path) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    try:
        with application.database.transaction() as connection:
            TurnRepository(connection).add(
                Turn(
                    thread_id=thread_id,
                    ordinal=1,
                    prompt="queued work",
                )
            )

        with pytest.raises(ConflictError) as raised:
            application.deletions.delete(thread_id)
        codes = {item["code"] for item in raised.value.details["blockers"]}
        assert "ACTIVE_TURN" in codes
        assert application.session_store.get_session(thread_id) is not None
    finally:
        application.close()


def test_active_goal_blocks_deletion_but_paused_goal_can_be_deleted(
    tmp_path: Path,
) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    try:
        goal = application.goals.create(
            thread_id,
            objective="Finish the task",
            start=False,
        )
        with pytest.raises(ConflictError) as raised:
            application.deletions.delete(thread_id)
        codes = {item["code"] for item in raised.value.details["blockers"]}
        assert "ACTIVE_GOAL" in codes

        application.goals.pause(
            thread_id,
            expected_goal_id=goal.id,
        )
        application.deletions.delete(thread_id)
        assert application.session_store.get_session(thread_id) is None
    finally:
        application.close()


def test_automation_and_worktree_ownership_block_implicit_orphans(
    tmp_path: Path,
) -> None:
    application, project_id, thread_id = _application(tmp_path)
    try:
        created = application.automations.create(
            project_id=project_id,
            name="Nightly check",
            prompt="inspect the repository",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        with pytest.raises(ConflictError) as automation_error:
            application.deletions.delete(created.thread.id)
        codes = {item["code"] for item in automation_error.value.details["blockers"]}
        assert "AUTOMATION_ATTACHED" in codes

        with application.database.transaction() as connection:
            threads = ThreadRepository(connection)
            thread = threads.get(thread_id)
            assert thread is not None
            threads.update(
                replace(
                    thread,
                    worktree_path=str(tmp_path / "owned-worktree"),
                )
            )
        with pytest.raises(ConflictError) as worktree_error:
            application.deletions.delete(thread_id)
        codes = {item["code"] for item in worktree_error.value.details["blockers"]}
        assert "WORKTREE_ATTACHED" in codes
    finally:
        application.close()


def test_cleanup_failure_keeps_tombstone_for_next_startup(tmp_path: Path) -> None:
    application, _project_id, thread_id = _application(tmp_path)
    try:
        with patch.object(SessionDeletionJournal, "finalize", return_value=False):
            result = application.deletions.delete(thread_id)
        assert result.cleanup_pending is True
        assert application.session_store.get_session(thread_id) is None
        assert application.session_store.is_deletion_pending(thread_id) is True
    finally:
        application.close()

    recovered = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=SessionStore(tmp_path / "sessions"),
    )
    try:
        assert recovered.session_store.pending_deletions() == ()
        assert _row_count(recovered, "threads", thread_id) == 0
    finally:
        recovered.close()
