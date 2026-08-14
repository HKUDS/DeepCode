from __future__ import annotations

from pathlib import Path

from core.domain import Project, Thread, ThreadMode, Turn
from core.persistence import (
    Database,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
)
from core.persistence.migrations import current_version, migrate


def _seed_v14(database: Database, workspace: Path) -> tuple[Thread, Turn]:
    project = Project(canonical_path=str(workspace), display_name="Migration")
    thread = Thread(
        project_id=project.id,
        title="Migration",
        mode=ThreadMode.CODE,
        workspace_path=project.canonical_path,
    )
    turn = Turn(thread_id=thread.id, ordinal=1, prompt="Legacy Turn")
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)
    return thread, turn


def _column_names(connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_v16_removes_the_abandoned_v15_web_policy_without_losing_rows(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=14)
    thread, turn = _seed_v14(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 15)
        connection.execute(
            "UPDATE threads SET web_search_mode_override = 'live' WHERE id = ?",
            (thread.id,),
        )
        connection.execute(
            "UPDATE turns SET web_access_policy_json = ? WHERE id = ?",
            ('{"mode":"live"}', turn.id),
        )
        migrate(connection, 16)

        assert current_version(connection) == 16
        assert "web_search_mode_override" not in _column_names(connection, "threads")
        assert "web_access_policy_json" not in _column_names(connection, "turns")
        assert ThreadRepository(connection).get(thread.id) == thread
        assert TurnRepository(connection).get(turn.id) == turn


def test_fresh_v16_schema_has_no_web_search_policy_columns(tmp_path: Path) -> None:
    database = Database(tmp_path / "fresh.sqlite3")
    database.initialize()

    with database.read() as connection:
        assert current_version(connection) == 16
        assert "web_search_mode_override" not in _column_names(connection, "threads")
        assert "web_access_policy_json" not in _column_names(connection, "turns")


def test_v16_downgrade_restores_empty_v15_compatibility_columns(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "downgrade.sqlite3")
    database.initialize()
    thread, turn = _seed_v14(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 15)

        assert current_version(connection) == 15
        assert "web_search_mode_override" in _column_names(connection, "threads")
        assert "web_access_policy_json" in _column_names(connection, "turns")
        assert (
            connection.execute(
                "SELECT web_search_mode_override FROM threads WHERE id = ?",
                (thread.id,),
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute(
                "SELECT web_access_policy_json FROM turns WHERE id = ?",
                (turn.id,),
            ).fetchone()[0]
            is None
        )

        migrate(connection, 16)
        assert "web_search_mode_override" not in _column_names(connection, "threads")
        assert "web_access_policy_json" not in _column_names(connection, "turns")
