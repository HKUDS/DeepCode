from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.domain import (
    Project,
    Thread,
    ThreadMode,
    Turn,
    TurnExecutor,
    WorkflowRun,
)
from core.persistence import (
    Database,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
    WorkflowRepository,
)
from core.persistence.migrations import current_version, migrate


def test_v11_backfills_typed_executors_and_round_trips_to_v10(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=10)
    project = Project(
        canonical_path=str(tmp_path / "workspace"),
        display_name="Executor migration",
    )
    thread = Thread(
        project_id=project.id,
        title="Executor migration",
        mode=ThreadMode.PAPER,
        workspace_path=project.canonical_path,
    )
    agent_turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Agent Turn",
    )
    legacy_workflow_turn = Turn(
        thread_id=thread.id,
        ordinal=2,
        prompt="Legacy Workflow Turn",
    )
    workflow = WorkflowRun(
        thread_id=thread.id,
        turn_id=legacy_workflow_turn.id,
        kind="paper2code",
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        turns = TurnRepository(connection)
        turns.add(agent_turn)
        turns.add(legacy_workflow_turn)
        WorkflowRepository(connection).add(workflow)

    with database.read() as connection:
        migrate(connection, 11)
        assert current_version(connection) == 11
        rows = connection.execute(
            "SELECT id, executor FROM turns ORDER BY ordinal"
        ).fetchall()
        assert [(row["id"], row["executor"]) for row in rows] == [
            (agent_turn.id, TurnExecutor.AGENT.value),
            (legacy_workflow_turn.id, TurnExecutor.WORKFLOW.value),
        ]
        turns = TurnRepository(connection)
        assert turns.get(agent_turn.id).executor is TurnExecutor.AGENT
        assert (
            turns.get(legacy_workflow_turn.id).executor
            is TurnExecutor.WORKFLOW
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE turns SET executor = 'unknown' WHERE id = ?",
                (agent_turn.id,),
            )

        migrate(connection, 10)
        assert current_version(connection) == 10
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turns)")
        }
        assert "executor" not in columns
        assert (
            TurnRepository(connection).get(legacy_workflow_turn.id).executor
            is TurnExecutor.AGENT
        )

        migrate(connection, 11)
        assert (
            TurnRepository(connection).get(legacy_workflow_turn.id).executor
            is TurnExecutor.WORKFLOW
        )


def test_v11_repository_persists_new_workflow_executor(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    project = Project(
        canonical_path=str(tmp_path / "workspace"),
        display_name="Executor round trip",
    )
    thread = Thread(
        project_id=project.id,
        title="Executor round trip",
        mode=ThreadMode.PAPER,
        workspace_path=project.canonical_path,
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Run Workflow",
        executor=TurnExecutor.WORKFLOW,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)

    with database.read() as connection:
        assert TurnRepository(connection).get(turn.id) == turn
