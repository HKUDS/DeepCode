from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.domain import (
    Automation,
    AutomationOccurrence,
    AutomationRevision,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationTrigger,
    ExecutionPermissionMode,
    Project,
    Thread,
    ThreadMode,
    Turn,
)
from core.domain.common import new_id, utc_now
from core.persistence import (
    AutomationOccurrenceRepository,
    AutomationRepository,
    AutomationRevisionRepository,
    AutomationRunRepository,
    Database,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
)
from core.persistence.migrations import current_version, migrate


def _seed_v12_turns(database: Database, workspace: Path) -> tuple[Turn, Turn]:
    now = utc_now()
    project = Project(
        canonical_path=str(workspace),
        display_name="Permission migration",
    )
    thread = Thread(
        project_id=project.id,
        title="Permission migration",
        mode=ThreadMode.GOAL,
        workspace_path=project.canonical_path,
    )
    ordinary = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Ordinary Turn",
    )
    goal_id = new_id("goal")
    automatic = Turn(
        thread_id=thread.id,
        ordinal=2,
        prompt="Automation Turn",
        goal_id=goal_id,
    )
    automation_id = new_id("auto")
    revision = AutomationRevision(
        automation_id=automation_id,
        ordinal=1,
        instruction=automatic.prompt,
    )
    automation = Automation(
        id=automation_id,
        project_id=project.id,
        thread_id=thread.id,
        name="Permission migration",
        current_revision_id=revision.id,
        prompt=revision.instruction,
        schedule_kind=AutomationScheduleKind.MANUAL,
    )
    occurrence = AutomationOccurrence(
        automation_id=automation.id,
        kind=AutomationTrigger.MANUAL,
        occurrence_key="manual:migration",
        nominal_at=now,
        observed_at=now,
    )
    run = AutomationRun(
        automation_id=automation.id,
        revision_id=revision.id,
        occurrence_id=occurrence.id,
        thread_id=thread.id,
        goal_id=goal_id,
        turn_id=automatic.id,
        trigger=AutomationTrigger.MANUAL,
        status=AutomationRunStatus.BLOCKED,
        scheduled_for=now,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        turns = TurnRepository(connection)
        turns.add(ordinary)
        turns.add(automatic)
        AutomationRevisionRepository(connection).add(revision)
        AutomationRepository(connection).add(automation)
        AutomationOccurrenceRepository(connection).add(occurrence)
        AutomationRunRepository(connection).add(run)
    return ordinary, automatic


def test_v13_backfills_automation_only_and_persists_typed_permission(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=12)
    ordinary, automatic = _seed_v12_turns(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 13)
        assert current_version(connection) == 13
        turns = TurnRepository(connection)
        assert turns.get(ordinary.id).execution_permission_mode is None
        assert (
            turns.get(automatic.id).execution_permission_mode
            is ExecutionPermissionMode.DEFAULT
        )

    explicit = Turn(
        thread_id=ordinary.thread_id,
        ordinal=3,
        prompt="Explicit policy",
        execution_permission_mode=ExecutionPermissionMode.PLAN,
    )
    with database.transaction() as connection:
        TurnRepository(connection).add(explicit)
    with database.read() as connection:
        assert TurnRepository(connection).get(explicit.id) == explicit
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE turns SET execution_permission_mode = 'unknown' "
                "WHERE id = ?",
                (explicit.id,),
            )


def test_v13_turn_permission_migration_is_reversible(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=12)
    _, automatic = _seed_v12_turns(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 13)
        assert (
            TurnRepository(connection).get(automatic.id).execution_permission_mode
            is ExecutionPermissionMode.DEFAULT
        )

        migrate(connection, 12)
        assert current_version(connection) == 12
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turns)")
        }
        assert "execution_permission_mode" not in columns
        restored = TurnRepository(connection).get(automatic.id)
        assert restored.execution_permission_mode is None

        migrate(connection, 13)
        assert (
            TurnRepository(connection).get(automatic.id).execution_permission_mode
            is ExecutionPermissionMode.DEFAULT
        )
