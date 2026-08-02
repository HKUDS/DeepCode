from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.domain import (
    Approval,
    ApprovalCategory,
    ApprovalGrant,
    Item,
    ItemKind,
    ItemStatus,
    Project,
    Thread,
    ThreadMode,
    Turn,
)
from core.persistence import (
    ApprovalGrantRepository,
    ApprovalRepository,
    Database,
    ItemRepository,
    ProjectRepository,
    ThreadRepository,
    TurnRepository,
)
from core.persistence.migrations import current_version, migrate


def _seed_approval(database: Database, workspace: Path) -> tuple[Thread, Approval]:
    project = Project(
        canonical_path=str(workspace),
        display_name="Approval grants",
    )
    thread = Thread(
        project_id=project.id,
        title="Approval grants",
        mode=ThreadMode.CODE,
        workspace_path=project.canonical_path,
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt="Request write access",
    )
    item = Item(
        thread_id=thread.id,
        turn_id=turn.id,
        ordinal=1,
        kind=ItemKind.APPROVAL_REQUEST,
        status=ItemStatus.PENDING,
        summary="Approval required: write",
    )
    approval = Approval(
        thread_id=thread.id,
        turn_id=turn.id,
        item_id=item.id,
        category=ApprovalCategory.FILE_WRITE,
        request={"toolName": "write"},
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)
        ItemRepository(connection).add(item)
        ApprovalRepository(connection).add(approval)
    return thread, approval


def test_v12_approval_grants_round_trip_and_enforce_thread_scope(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=11)
    thread, approval = _seed_approval(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 12)
        assert current_version(connection) == 12

    grant = ApprovalGrant(
        thread_id=thread.id,
        tool_name="write",
        source_approval_id=approval.id,
    )
    with database.transaction() as connection:
        grants = ApprovalGrantRepository(connection)
        assert grants.add_if_absent(grant) is True
        assert grants.add_if_absent(grant) is False
        assert grants.allows(thread.id, "write") is True
        assert grants.allows(thread.id, "shell") is False
        assert grants.list_for_thread(thread.id) == [grant]

    second_project = Project(
        canonical_path=str(tmp_path / "other-workspace"),
        display_name="Other",
    )
    second_thread = Thread(
        project_id=second_project.id,
        title="Other",
        mode=ThreadMode.CODE,
        workspace_path=second_project.canonical_path,
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(second_project)
        ThreadRepository(connection).add(second_thread)

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            ApprovalGrantRepository(connection).add_if_absent(
                ApprovalGrant(
                    thread_id=second_thread.id,
                    tool_name="write",
                    source_approval_id=approval.id,
                )
            )

    with database.transaction() as connection:
        assert ThreadRepository(connection).remove(thread.id) is True
    with database.read() as connection:
        assert ApprovalGrantRepository(connection).list_for_thread(thread.id) == []


def test_v12_approval_grants_migration_is_reversible(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=11)
    thread, approval = _seed_approval(database, tmp_path / "workspace")

    with database.read() as connection:
        migrate(connection, 12)
    with database.transaction() as connection:
        ApprovalGrantRepository(connection).add_if_absent(
            ApprovalGrant(
                thread_id=thread.id,
                tool_name="write",
                source_approval_id=approval.id,
            )
        )

    with database.read() as connection:
        migrate(connection, 11)
        assert current_version(connection) == 11
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'approval_grants'"
            ).fetchone()
            is None
        )
        assert ApprovalRepository(connection).get(approval.id) == approval

        migrate(connection, 12)
        assert current_version(connection) == 12
        assert ApprovalGrantRepository(connection).list_for_thread(thread.id) == []
