from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from core.domain import Project, Thread, ThreadMode, Turn
from core.domain.automation import (
    Automation,
    AutomationOccurrence,
    AutomationRevision,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationTrigger,
)
from core.domain.common import utc_now
from core.persistence import (
    AutomationRepository,
    AutomationRunRepository,
    Database,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
)
from core.persistence.automation_repository import (
    AutomationOccurrenceRepository,
    AutomationRevisionRepository,
)
from core.persistence.migrations import current_version, migrate
from core.persistence.serde import dump_datetime


def _seed_open_run(
    database: Database,
    tmp_path: Path,
    *,
    suffix: str,
) -> tuple[Project, Thread, Automation, AutomationRun]:
    now = utc_now()
    project = Project(
        canonical_path=str(tmp_path / suffix),
        display_name=f"Automation {suffix}",
    )
    thread = Thread(
        project_id=project.id,
        title=f"Goal {suffix}",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )
    automation_id = f"auto_{suffix}"
    revision = AutomationRevision(
        automation_id=automation_id,
        ordinal=1,
        instruction="Inspect and verify the repository",
    )
    automation = Automation(
        id=automation_id,
        project_id=project.id,
        thread_id=thread.id,
        name=f"Definition {suffix}",
        current_revision_id=revision.id,
        prompt=revision.instruction,
        schedule_kind=AutomationScheduleKind.MANUAL,
    )
    occurrence = AutomationOccurrence(
        automation_id=automation.id,
        kind=AutomationTrigger.MANUAL,
        occurrence_key=f"manual:{suffix}",
        nominal_at=now,
        observed_at=now,
    )
    run = AutomationRun(
        automation_id=automation.id,
        revision_id=revision.id,
        occurrence_id=occurrence.id,
        goal_id=f"goal_{suffix}",
        thread_id=thread.id,
        trigger=AutomationTrigger.MANUAL,
        status=AutomationRunStatus.BLOCKED,
        scheduled_for=now,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        AutomationRevisionRepository(connection).add(revision)
        AutomationRepository(connection).add(automation)
        AutomationOccurrenceRepository(connection).add(occurrence)
        AutomationRunRepository(connection).add(run)
    return project, thread, automation, run


def _add_turn(
    connection: sqlite3.Connection,
    run: AutomationRun,
    *,
    ordinal: int,
) -> Turn:
    turn = Turn(
        thread_id=run.thread_id,
        ordinal=ordinal,
        prompt=f"Automation turn {ordinal}",
        goal_id=run.goal_id,
    )
    TurnRepository(connection).add(turn)
    return turn


def test_run_updates_use_version_as_compare_and_set(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    _, _, _, original = _seed_open_run(database, tmp_path, suffix="version")

    changed = replace(original, detail="first writer")
    with database.transaction() as connection:
        runs = AutomationRunRepository(connection)
        assert runs.update(changed)
        persisted = runs.get(original.id)
        assert persisted is not None
        assert persisted.detail == "first writer"
        assert persisted.version == original.version + 1

        stale = replace(original, detail="stale writer")
        assert not runs.update(stale)
        assert runs.get(original.id) == persisted


def test_open_run_may_bind_turn_once_but_cannot_reassign_it(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    _, _, _, original = _seed_open_run(database, tmp_path, suffix="turn_owner")

    with database.transaction() as connection:
        runs = AutomationRunRepository(connection)
        first = _add_turn(connection, original, ordinal=1)
        second = _add_turn(connection, original, ordinal=2)

        assert runs.update(replace(original, turn_id=first.id))
        bound = runs.get(original.id)
        assert bound is not None
        assert bound.turn_id == first.id
        assert bound.version == original.version + 1

        with pytest.raises(
            sqlite3.IntegrityError,
            match="Turn ownership is immutable",
        ):
            runs.update(replace(bound, turn_id=second.id))


def test_terminal_run_cannot_be_updated(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    _, _, _, original = _seed_open_run(database, tmp_path, suffix="terminal")
    completed_at = utc_now()

    with database.transaction() as connection:
        runs = AutomationRunRepository(connection)
        assert runs.update(
            replace(
                original,
                status=AutomationRunStatus.COMPLETED,
                detail="finished",
                updated_at=completed_at,
                completed_at=completed_at,
            )
        )
        terminal = runs.get(original.id)
        assert terminal is not None
        assert terminal.status is AutomationRunStatus.COMPLETED

        with pytest.raises(
            sqlite3.IntegrityError,
            match="terminal automation runs are immutable",
        ):
            runs.update(replace(terminal, detail="rewritten terminal result"))


def test_run_identity_cannot_be_mutated_even_with_a_valid_version_step(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    _, _, _, run = _seed_open_run(database, tmp_path, suffix="identity")

    with (
        pytest.raises(
            sqlite3.IntegrityError,
            match="automation run identity is immutable",
        ),
        database.transaction() as connection,
    ):
        connection.execute(
            "UPDATE automation_runs "
            "SET goal_id = ?, version = version + 1 WHERE id = ?",
            ("goal_replacement", run.id),
        )


def test_deleting_thread_cascades_a_linked_terminal_run(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    _, thread, automation, open_run = _seed_open_run(
        database,
        tmp_path,
        suffix="cascade",
    )
    completed_at = utc_now()

    with database.transaction() as connection:
        runs = AutomationRunRepository(connection)
        turn = _add_turn(connection, open_run, ordinal=1)
        assert runs.update(replace(open_run, turn_id=turn.id))
        bound = runs.get(open_run.id)
        assert bound is not None
        assert runs.update(
            replace(
                bound,
                status=AutomationRunStatus.COMPLETED,
                updated_at=completed_at,
                completed_at=completed_at,
            )
        )

    with database.transaction() as connection:
        assert ThreadRepository(connection).remove(thread.id)

    with database.read() as connection:
        assert AutomationRepository(connection).get(automation.id) is None
        assert AutomationRunRepository(connection).get(open_run.id) is None
        assert TurnRepository(connection).get(turn.id) is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v9_downgrade_to_v8_preserves_run_and_removes_v9_invariants(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=9)
    _, _, _, run = _seed_open_run(database, tmp_path, suffix="downgrade")

    with database.read() as connection:
        assert current_version(connection) == 9
        migrate(connection, 8)
        assert current_version(connection) == 8

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(automation_runs)")
        }
        assert "version" not in columns

        trigger_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'automation_runs'"
            )
        }
        assert trigger_names.isdisjoint(
            {
                "prevent_automation_run_identity_update",
                "prevent_automation_run_turn_reassignment",
                "prevent_automation_run_terminal_update",
                "enforce_automation_run_version_update",
            }
        )

        persisted = connection.execute(
            "SELECT id, goal_id, status, detail FROM automation_runs WHERE id = ?",
            (run.id,),
        ).fetchone()
        assert tuple(persisted) == (
            run.id,
            run.goal_id,
            run.status.value,
            run.detail,
        )


def test_v8_migration_deduplicates_legacy_runs_with_the_same_goal(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=7)
    now = dump_datetime(utc_now())
    project = Project(
        canonical_path=str(tmp_path / "legacy"),
        display_name="Legacy",
    )
    thread = Thread(
        project_id=project.id,
        title="Legacy goal",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )
    legacy_turn = Turn(
        id="turn_legacy_shared_goal",
        thread_id=thread.id,
        ordinal=1,
        prompt="Legacy automation",
        goal_id="goal_legacy_shared",
    )

    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(legacy_turn)
        connection.execute(
            "INSERT INTO automations ("
            "id, project_id, thread_id, name, prompt, status, schedule_kind, "
            "interval_seconds, next_run_at, last_run_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "auto_legacy_duplicate",
                project.id,
                thread.id,
                "Legacy duplicate Goal",
                "Legacy automation",
                "enabled",
                "manual",
                None,
                None,
                None,
                now,
                now,
            ),
        )
        for run_id in ("arun_legacy_a", "arun_legacy_z"):
            connection.execute(
                "INSERT INTO automation_runs ("
                "id, automation_id, thread_id, turn_id, trigger, status, "
                "scheduled_for, detail, created_at, updated_at, "
                "started_at, completed_at"
                ") VALUES (?, ?, ?, ?, 'manual', 'completed', ?, '', ?, ?, ?, ?)",
                (
                    run_id,
                    "auto_legacy_duplicate",
                    thread.id,
                    legacy_turn.id,
                    now,
                    now,
                    now,
                    now,
                    now,
                ),
            )

    with database.read() as connection:
        migrate(connection, 8)
        assert current_version(connection) == 8
        rows = connection.execute(
            "SELECT id, goal_id, turn_id, status, detail "
            "FROM automation_runs ORDER BY id"
        ).fetchall()
        assert len(rows) == 2

        loser, winner = rows
        assert winner["id"] == "arun_legacy_z"
        assert winner["goal_id"] == legacy_turn.goal_id
        assert winner["turn_id"] == legacy_turn.id
        assert winner["status"] == "completed"

        assert loser["id"] == "arun_legacy_a"
        assert loser["goal_id"] is None
        assert loser["turn_id"] is None
        assert loser["status"] == "interrupted"
        assert "duplicate Goal ownership" in loser["detail"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
