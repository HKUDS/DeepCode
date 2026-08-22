from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from core.domain.common import utc_now
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionPermissionRuleAction,
    ExecutionPermissionRuleSnapshot,
    ExecutionSecurityProfile,
)
from core.domain.project import Project
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn, TurnStatus
from core.persistence.database import Database
from core.persistence.execution_repository import TurnRepository
from core.persistence.migrations import current_version, migrate
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository


def _fail_closed_legacy_profile(
    mode: ExecutionPermissionMode,
) -> ExecutionSecurityProfile:
    return ExecutionSecurityProfile.from_legacy(
        mode,
        command_sandbox=True,
        permission_rules=(
            ExecutionPermissionRuleSnapshot(
                permission="*",
                pattern="*",
                action=ExecutionPermissionRuleAction.DENY,
            ),
        ),
    )


def _seed_v13_session(
    database: Database,
    workspace: Path,
) -> tuple[Thread, Turn]:
    project = Project(
        canonical_path=str(workspace),
        display_name="Execution security migration",
    )
    thread = Thread(
        project_id=project.id,
        title="Execution security migration",
        mode=ThreadMode.CODE,
        workspace_path=str(workspace),
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Legacy turn",
        execution_permission_mode=ExecutionPermissionMode.FULL_AUTO,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)
    return thread, turn


def test_v14_adds_session_override_and_immutable_turn_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=13)
    legacy_thread, legacy_turn = _seed_v13_session(database, workspace)
    unknown_mode_thread = Thread(
        project_id=legacy_thread.project_id,
        title="Unknown legacy security",
        mode=ThreadMode.CODE,
        workspace_path=str(workspace),
    )
    unknown_mode_turn = Turn(
        thread_id=unknown_mode_thread.id,
        ordinal=1,
        prompt="Legacy turn without a permission mode",
    )
    terminal_turn = Turn(
        thread_id=legacy_thread.id,
        ordinal=2,
        prompt="Completed legacy turn",
        execution_permission_mode=ExecutionPermissionMode.DEFAULT,
        status=TurnStatus.COMPLETED,
        completed_at=utc_now(),
    )
    with database.transaction() as connection:
        ThreadRepository(connection).add(unknown_mode_thread)
        TurnRepository(connection).add(unknown_mode_turn)
        TurnRepository(connection).add(terminal_turn)

    with database.read() as connection:
        migrate(connection, 14)
        assert current_version(connection) == 14
        assert (
            ThreadRepository(connection).get(legacy_thread.id).access_preset_override
            is None
        )
        assert TurnRepository(connection).get(
            legacy_turn.id
        ).execution_security_profile == _fail_closed_legacy_profile(
            ExecutionPermissionMode.FULL_AUTO,
        )
        assert TurnRepository(connection).get(
            unknown_mode_turn.id
        ).execution_security_profile == ExecutionSecurityProfile.for_preset(
            ExecutionAccessPreset.READ_ONLY
        )
        assert (
            TurnRepository(connection).get(terminal_turn.id).execution_security_profile
            is None
        )

    full_access = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)
    updated_thread = replace(
        legacy_thread,
        access_preset_override=ExecutionAccessPreset.FULL_ACCESS,
    )
    snapshotted_turn = Turn(
        thread_id=legacy_thread.id,
        ordinal=3,
        prompt="Turn with an immutable access snapshot",
        execution_permission_mode=full_access.permission_mode,
        execution_security_profile=full_access,
    )
    with database.transaction() as connection:
        ThreadRepository(connection).update(updated_thread)
        TurnRepository(connection).add(snapshotted_turn)

    with database.read() as connection:
        assert ThreadRepository(connection).get(updated_thread.id) == updated_thread
        assert TurnRepository(connection).get(snapshotted_turn.id) == snapshotted_turn
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE threads SET access_preset_override = 'unknown' WHERE id = ?",
                (updated_thread.id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE turns SET execution_security_profile_json = '' WHERE id = ?",
                (snapshotted_turn.id,),
            )
        for corrupt_snapshot in ("{}", "{not-json"):
            connection.execute(
                "UPDATE turns SET execution_security_profile_json = ? WHERE id = ?",
                (corrupt_snapshot, snapshotted_turn.id),
            )
            with pytest.raises(
                ValueError,
                match="persisted execution security profile is invalid",
            ):
                TurnRepository(connection).get(snapshotted_turn.id)


def test_v14_session_execution_security_migration_is_reversible(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=13)
    thread, turn = _seed_v13_session(database, workspace)

    with database.read() as connection:
        migrate(connection, 14)
        assert current_version(connection) == 14

        migrate(connection, 13)
        assert current_version(connection) == 13
        assert "access_preset_override" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(threads)")
        }
        assert "execution_security_profile_json" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(turns)")
        }
        assert ThreadRepository(connection).get(thread.id) == thread
        assert TurnRepository(connection).get(turn.id) == turn

        migrate(connection, 14)
        assert current_version(connection) == 14
        assert (
            ThreadRepository(connection).get(thread.id).access_preset_override is None
        )
        assert TurnRepository(connection).get(
            turn.id
        ).execution_security_profile == _fail_closed_legacy_profile(
            ExecutionPermissionMode.FULL_AUTO,
        )


def test_v17_widens_preset_override_check_to_dangerous_skip(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=16)
    thread, _ = _seed_v13_session(database, workspace)

    with database.read() as connection:
        # v16 CHECK rejects the new value.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE threads SET access_preset_override = 'dangerous_skip' "
                "WHERE id = ?",
                (thread.id,),
            )

        migrate(connection, 17)
        assert current_version(connection) == 17
        connection.execute(
            "UPDATE threads SET access_preset_override = 'dangerous_skip' "
            "WHERE id = ?",
            (thread.id,),
        )
        assert ThreadRepository(connection).get(
            thread.id
        ).access_preset_override == ExecutionAccessPreset.DANGEROUS_SKIP
        # Unknown values are still rejected after the widen.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE threads SET access_preset_override = 'unknown' WHERE id = ?",
                (thread.id,),
            )


def test_v17_downgrade_clears_dangerous_skip_values(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=16)
    thread, _ = _seed_v13_session(database, workspace)

    with database.read() as connection:
        migrate(connection, 17)
        connection.execute(
            "UPDATE threads SET access_preset_override = 'dangerous_skip' "
            "WHERE id = ?",
            (thread.id,),
        )

        migrate(connection, 16)
        assert current_version(connection) == 16
        # The downgrade backup keeps only non-dangerous values, so the
        # dangerous override is cleared rather than left violating v16 CHECK.
        assert (
            ThreadRepository(connection).get(thread.id).access_preset_override is None
        )

        migrate(connection, 17)
        assert (
            ThreadRepository(connection).get(thread.id).access_preset_override is None
        )
