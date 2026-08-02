from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
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
    AutomationStatus,
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


def _add_definition(
    connection: sqlite3.Connection,
    tmp_path: Path,
    *,
    suffix: str,
    instruction: str = "Inspect the repository",
) -> tuple[Project, Thread, AutomationRevision, Automation]:
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
        instruction=instruction,
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
    ProjectRepository(connection).add(project)
    ThreadRepository(connection).add(thread)
    AutomationRevisionRepository(connection).add(revision)
    AutomationRepository(connection).add(automation)
    return project, thread, revision, automation


def test_v8_migrates_legacy_execution_facts_and_round_trips_to_v7(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=7)
    now = dump_datetime(utc_now())
    project = Project(canonical_path=str(tmp_path), display_name="Legacy")
    thread = Thread(
        project_id=project.id,
        title="Legacy goal",
        mode=ThreadMode.GOAL,
        workspace_path=str(tmp_path),
    )

    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        legacy_turn = Turn(
            id="turn_legacy_goal",
            thread_id=thread.id,
            ordinal=1,
            prompt="Legacy instruction",
            goal_id="goal_legacy_automation",
        )
        TurnRepository(connection).add(legacy_turn)
        connection.execute(
            "INSERT INTO automations ("
            "id, project_id, thread_id, name, prompt, status, schedule_kind, "
            "interval_seconds, next_run_at, last_run_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "auto_legacy",
                project.id,
                thread.id,
                "Legacy definition",
                "Legacy instruction",
                "enabled",
                "interval",
                60,
                now,
                None,
                now,
                now,
            ),
        )
        for run_id, turn_id in (
            ("arun_legacy_first", legacy_turn.id),
            ("arun_legacy_second", None),
        ):
            connection.execute(
                "INSERT INTO automation_runs ("
                "id, automation_id, thread_id, turn_id, trigger, status, "
                "scheduled_for, detail, created_at, updated_at, "
                "started_at, completed_at"
                ") VALUES (?, ?, ?, ?, 'scheduled', 'queued', ?, '', ?, ?, NULL, NULL)",
                (run_id, "auto_legacy", thread.id, turn_id, now, now, now),
            )

    with database.read() as connection:
        migrate(connection, 8)
        assert current_version(connection) == 8
        assert "prompt" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(automations)")
        }
        definition = connection.execute(
            "SELECT automation.current_revision_id, revision.ordinal, "
            "revision.instruction "
            "FROM automations AS automation "
            "JOIN automation_revisions AS revision "
            "ON revision.id = automation.current_revision_id"
        ).fetchone()
        assert tuple(definition) == (
            "arev_legacy_auto_legacy",
            1,
            "Legacy instruction",
        )
        occurrences = connection.execute(
            "SELECT occurrence_key FROM automation_occurrences ORDER BY occurrence_key"
        ).fetchall()
        assert [row[0] for row in occurrences] == [
            "legacy:arun_legacy_first",
            "legacy:arun_legacy_second",
        ]
        runs = connection.execute(
            "SELECT id, status, completed_at, goal_id FROM automation_runs ORDER BY id"
        ).fetchall()
        assert [row["status"] for row in runs].count("queued") == 1
        assert [row["status"] for row in runs].count("interrupted") == 1
        assert {row["id"]: row["goal_id"] for row in runs} == {
            "arun_legacy_first": legacy_turn.goal_id,
            "arun_legacy_second": None,
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        connection.execute(
            "UPDATE automations SET status = 'retired', next_run_at = NULL "
            "WHERE id = 'auto_legacy'"
        )
        connection.execute(
            "UPDATE automation_runs SET status = 'blocked' WHERE status = 'queued'"
        )
        migrate(connection, 7)
        assert current_version(connection) == 7
        restored = connection.execute(
            "SELECT prompt, status, next_run_at FROM automations "
            "WHERE id = 'auto_legacy'"
        ).fetchone()
        assert tuple(restored) == ("Legacy instruction", "paused", None)
        assert {
            row[0] for row in connection.execute("SELECT status FROM automation_runs")
        } == {"interrupted", "waiting"}
        assert (
            connection.execute("SELECT COUNT(*) FROM automation_runs").fetchone()[0]
            == 2
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_revision_history_is_explicit_immutable_and_projects_prompt(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        _, _, first, automation = _add_definition(
            connection,
            tmp_path,
            suffix="revision",
            instruction="First instruction",
        )

    with database.transaction() as connection:
        revisions = AutomationRevisionRepository(connection)
        second = AutomationRevision(
            automation_id=automation.id,
            ordinal=revisions.next_ordinal(automation.id),
            instruction="Second instruction",
        )
        revisions.add(second)
        published = replace(
            automation,
            current_revision_id=second.id,
            prompt=second.instruction,
            updated_at=automation.updated_at + timedelta(seconds=1),
        )
        assert AutomationRepository(connection).update(
            published,
            expected_current_revision_id=first.id,
            expected_updated_at=automation.updated_at,
        )

    renamed = replace(
        published,
        name="Renamed definition",
        updated_at=published.updated_at + timedelta(seconds=1),
    )
    stale = replace(
        published,
        name="Stale overwrite",
        updated_at=renamed.updated_at + timedelta(seconds=1),
    )
    with database.transaction() as connection:
        definitions = AutomationRepository(connection)
        assert definitions.update(
            renamed,
            expected_updated_at=published.updated_at,
        )
        assert not definitions.update(
            stale,
            expected_current_revision_id=first.id,
        )
        assert not definitions.update(
            stale,
            expected_updated_at=published.updated_at,
        )

    with database.read() as connection:
        definitions = AutomationRepository(connection)
        revisions = AutomationRevisionRepository(connection)
        current = definitions.get(automation.id)
        assert current is not None
        assert current.name == "Renamed definition"
        assert current.current_revision_id == second.id
        assert current.prompt == "Second instruction"
        assert current.updated_at == renamed.updated_at
        assert revisions.get_current(automation.id) == second
        assert revisions.list_for_automation(automation.id) == [first, second]

    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        database.transaction() as connection,
    ):
        connection.execute(
            "UPDATE automation_revisions SET instruction = 'mutated' WHERE id = ?",
            (first.id,),
        )

    with (
        pytest.raises(ValueError, match="prompt projection"),
        database.transaction() as connection,
    ):
        AutomationRepository(connection).update(
            replace(automation, prompt="Unversioned mutation")
        )


def test_occurrence_and_open_run_constraints_and_cas_turn_attachment(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    with database.transaction() as connection:
        _, thread, revision, automation = _add_definition(
            connection,
            tmp_path,
            suffix="runs",
        )
        occurrence = AutomationOccurrence(
            automation_id=automation.id,
            kind=AutomationTrigger.MANUAL,
            occurrence_key="manual:request-1",
            nominal_at=now,
            observed_at=now,
        )
        run = AutomationRun(
            automation_id=automation.id,
            revision_id=revision.id,
            occurrence_id=occurrence.id,
            goal_id="goal_run_one",
            thread_id=thread.id,
            trigger=AutomationTrigger.MANUAL,
            status=AutomationRunStatus.BLOCKED,
            scheduled_for=now,
        )
        AutomationOccurrenceRepository(connection).add(occurrence)
        AutomationRunRepository(connection).add(run)

    with database.read() as connection:
        occurrences = AutomationOccurrenceRepository(connection)
        runs = AutomationRunRepository(connection)
        assert (
            occurrences.get_by_key(
                automation.id,
                AutomationTrigger.MANUAL,
                "manual:request-1",
            )
            == occurrence
        )
        assert runs.get_for_occurrence(occurrence.id) == run
        assert runs.get_for_goal("goal_run_one") == run
        assert runs.open_for_automation(automation.id) == run
        assert runs.list_active() == [run]

    duplicate = replace(occurrence, id="aocc_duplicate")
    with (
        pytest.raises(sqlite3.IntegrityError),
        database.transaction() as connection,
    ):
        AutomationOccurrenceRepository(connection).add(duplicate)

    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        database.transaction() as connection,
    ):
        connection.execute(
            "UPDATE automation_occurrences SET occurrence_key = ? WHERE id = ?",
            ("manual:mutated", occurrence.id),
        )

    second_occurrence = AutomationOccurrence(
        automation_id=automation.id,
        kind=AutomationTrigger.MANUAL,
        occurrence_key="manual:request-2",
        nominal_at=now,
        observed_at=now,
    )
    conflicting = replace(
        run,
        id="arun_second_open",
        occurrence_id=second_occurrence.id,
        goal_id="goal_run_two",
    )
    with (
        pytest.raises(sqlite3.IntegrityError),
        database.transaction() as connection,
    ):
        AutomationOccurrenceRepository(connection).add(second_occurrence)
        AutomationRunRepository(connection).add(conflicting)

    completed_at = utc_now()
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt=automation.prompt,
        goal_id=run.goal_id,
    )
    with database.transaction() as connection:
        runs = AutomationRunRepository(connection)
        TurnRepository(connection).add(turn)
        attached = replace(
            run,
            turn_id=turn.id,
            updated_at=completed_at,
        )
        assert runs.update(
            attached,
            expected_status=AutomationRunStatus.BLOCKED,
            expected_updated_at=run.updated_at,
        )
        persisted_attached = replace(attached, version=run.version + 1)
        completed = replace(
            persisted_attached,
            status=AutomationRunStatus.COMPLETED,
            completed_at=completed_at,
            updated_at=completed_at + timedelta(seconds=1),
        )
        assert runs.update(
            completed,
            expected_status=AutomationRunStatus.BLOCKED,
            expected_updated_at=persisted_attached.updated_at,
        )
        stale = replace(
            run,
            status=AutomationRunStatus.RUNNING,
            started_at=completed_at,
            updated_at=completed_at + timedelta(seconds=1),
        )
        assert not runs.update(
            stale,
            expected_status=AutomationRunStatus.BLOCKED,
        )
        assert not runs.update(
            stale,
            expected_updated_at=run.updated_at,
        )
        persisted_completed = replace(
            completed,
            version=persisted_attached.version + 1,
        )
        assert runs.get(run.id) == persisted_completed

    with (
        pytest.raises(KeyError),
        database.transaction() as connection,
    ):
        AutomationRunRepository(connection).update(
            replace(persisted_completed, id="arun_missing")
        )

    duplicate_occurrence_run = replace(
        run,
        id="arun_duplicate_occurrence",
        goal_id="goal_duplicate_occurrence",
        status=AutomationRunStatus.COMPLETED,
        completed_at=completed_at,
        updated_at=completed_at,
    )
    with (
        pytest.raises(sqlite3.IntegrityError),
        database.transaction() as connection,
    ):
        AutomationRunRepository(connection).add(duplicate_occurrence_run)

    goal_occurrence = replace(
        second_occurrence,
        id="aocc_duplicate_goal",
        occurrence_key="manual:duplicate-goal",
    )
    duplicate_goal_run = replace(
        run,
        id="arun_duplicate_goal",
        occurrence_id=goal_occurrence.id,
        status=AutomationRunStatus.QUEUED,
        completed_at=None,
    )
    with (
        pytest.raises(sqlite3.IntegrityError),
        database.transaction() as connection,
    ):
        AutomationOccurrenceRepository(connection).add(goal_occurrence)
        AutomationRunRepository(connection).add(duplicate_goal_run)


def test_retired_definition_is_hidden_but_parent_delete_cascades_history(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    with database.transaction() as connection:
        _, thread, revision, automation = _add_definition(
            connection,
            tmp_path,
            suffix="retire",
        )
        occurrence = AutomationOccurrence(
            automation_id=automation.id,
            kind=AutomationTrigger.MANUAL,
            occurrence_key="manual:retire-test",
            nominal_at=now,
            observed_at=now,
        )
        run = AutomationRun(
            automation_id=automation.id,
            revision_id=revision.id,
            occurrence_id=occurrence.id,
            thread_id=thread.id,
            trigger=AutomationTrigger.MANUAL,
            status=AutomationRunStatus.COMPLETED,
            scheduled_for=now,
            completed_at=now,
        )
        AutomationOccurrenceRepository(connection).add(occurrence)
        AutomationRunRepository(connection).add(run)
        assert AutomationRepository(connection).remove(automation.id)

    with database.read() as connection:
        definitions = AutomationRepository(connection)
        assert definitions.get(automation.id) is None
        retired = definitions.get(automation.id, include_retired=True)
        assert retired is not None
        assert retired.status is AutomationStatus.RETIRED
        assert AutomationRevisionRepository(connection).get(revision.id) == revision

    with (
        pytest.raises(KeyError),
        database.transaction() as connection,
    ):
        AutomationRepository(connection).update(automation)

    with database.transaction() as connection:
        assert ThreadRepository(connection).remove(thread.id)

    with database.read() as connection:
        assert (
            AutomationRepository(connection).get(
                automation.id,
                include_retired=True,
            )
            is None
        )
        assert AutomationRevisionRepository(connection).get(revision.id) is None
        assert AutomationOccurrenceRepository(connection).get(occurrence.id) is None
        assert AutomationRunRepository(connection).get(run.id) is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
