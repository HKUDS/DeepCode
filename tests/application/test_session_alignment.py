from __future__ import annotations

from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ProjectNotTrustedError
from core.domain.common import utc_now
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.project import Project
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn, TurnStatus
from core.persistence.database import Database
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.legacy_import_repository import LegacyImportRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore


def _seed_cli_session(store: SessionStore, workspace: Path, title: str = "CLI work"):
    session = store.create_session(
        title=title,
        metadata={"kind": "tui", "workspace": str(workspace)},
    )
    store.append_message(session.session_id, "user", "remember this")
    store.append_message(session.session_id, "assistant", "remembered")
    return store.get_session(session.session_id)


def test_desktop_projects_existing_cli_session_without_copying_it(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = _seed_cli_session(store, workspace)
    source = store.root / session.session_id / "session.jsonl"
    before = source.read_bytes()

    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    try:
        threads = application.threads.list()
        assert [thread.id for thread in threads] == [session.session_id]
        assert threads[0].workspace_path == str(workspace)
        assert source.read_bytes() == before

        with application.database.read() as connection:
            turns = TurnRepository(connection).list_for_thread(session.session_id)
            items = ItemRepository(connection).conversation_for_thread(
                session.session_id
            )
        assert len(turns) == 1
        assert [item.payload["text"] for item in items] == [
            "remember this",
            "remembered",
        ]
    finally:
        application.close()


def test_projection_database_can_be_deleted_and_rebuilt_from_jsonl(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = _seed_cli_session(store, workspace)
    database = tmp_path / "state.sqlite3"

    first = DeepCodeApplication.open(database, session_store=store)
    assert first.threads.read(session.session_id).id == session.session_id
    first.close()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database}{suffix}")
        if path.exists():
            path.unlink()

    rebuilt = DeepCodeApplication.open(database, session_store=store)
    try:
        thread = rebuilt.threads.read(session.session_id)
        assert thread.id == session.session_id
        with rebuilt.database.read() as connection:
            items = ItemRepository(connection).conversation_for_thread(thread.id)
        assert [item.payload["text"] for item in items] == [
            "remember this",
            "remembered",
        ]
    finally:
        rebuilt.close()


def test_session_listing_supports_exact_cwd_and_all_directories(tmp_path: Path) -> None:
    first_workspace = tmp_path / "one"
    second_workspace = tmp_path / "two"
    first_workspace.mkdir()
    second_workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    first = _seed_cli_session(store, first_workspace, "one")
    second = _seed_cli_session(store, second_workspace, "two")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    try:
        assert {thread.id for thread in application.threads.list()} == {
            first.session_id,
            second.session_id,
        }
        assert [
            thread.id for thread in application.threads.list(cwd=str(first_workspace))
        ] == [first.session_id]
    finally:
        application.close()


def test_explicit_cross_directory_resume_is_process_local_and_stable(
    tmp_path: Path,
) -> None:
    original_workspace = tmp_path / "original"
    resumed_workspace = tmp_path / "resumed"
    original_workspace.mkdir()
    resumed_workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    session = _seed_cli_session(store, original_workspace)
    source = store.root / session.session_id / "session.jsonl"
    before = source.read_bytes()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
    )
    try:
        resumed = application.threads.resume(
            session.session_id,
            workspace_path=str(resumed_workspace),
        )
        assert resumed.workspace_path == str(resumed_workspace)
        assert application.threads.read(session.session_id).workspace_path == str(
            resumed_workspace
        )
        listed = next(
            thread
            for thread in application.threads.list()
            if thread.id == session.session_id
        )
        assert listed.workspace_path == str(resumed_workspace)
        with pytest.raises(ProjectNotTrustedError):
            application.turns.start(session.session_id, prompt="must remain fenced")
        assert source.read_bytes() == before
        assert store.get_session(session.session_id).metadata["workspace"] == str(
            original_workspace
        )
    finally:
        application.close()


def test_p6_import_projection_tail_merges_back_into_original_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "sessions")
    original = _seed_cli_session(store, workspace)
    database_path = tmp_path / "state.sqlite3"
    database = Database(database_path)
    database.initialize()
    now = utc_now()
    project = Project(
        canonical_path=str(workspace),
        display_name="workspace",
        created_at=now,
        updated_at=now,
        last_opened_at=now,
    )
    imported_thread = Thread(
        id="thr_p6_projection",
        project_id=project.id,
        title=original.title,
        mode=ThreadMode.CODE,
        workspace_path=str(workspace),
        created_at=now,
        updated_at=now,
    )
    transcript = [
        ("user", "remember this"),
        ("assistant", "remembered"),
        ("user", "continued under P6"),
        ("assistant", "P6 continuation"),
    ]
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(imported_thread)
        turns = TurnRepository(connection)
        items = ItemRepository(connection)
        for ordinal, pair in enumerate((transcript[:2], transcript[2:]), start=1):
            turn = Turn(
                thread_id=imported_thread.id,
                ordinal=ordinal,
                prompt=pair[0][1],
                status=TurnStatus.COMPLETED,
                stop_reason="completed",
                started_at=now,
                completed_at=now,
            )
            turns.add(turn)
            for item_ordinal, (role, content) in enumerate(pair, start=1):
                items.add(
                    Item(
                        thread_id=imported_thread.id,
                        turn_id=turn.id,
                        ordinal=item_ordinal,
                        kind=(
                            ItemKind.USER_MESSAGE
                            if role == "user"
                            else ItemKind.ASSISTANT_MESSAGE
                        ),
                        status=ItemStatus.COMPLETED,
                        summary=content,
                        payload={"text": content},
                        created_at=now,
                        updated_at=now,
                    )
                )
        LegacyImportRepository(connection).add(
            source_key=f"{store.root.resolve()}::{original.session_id}",
            source_session_id=original.session_id,
            project_id=project.id,
            thread_id=imported_thread.id,
            imported_at=now,
        )

    application = DeepCodeApplication.open(database_path, session_store=store)
    try:
        visible = application.threads.list()
        assert [thread.id for thread in visible] == [original.session_id]
        recovered = store.get_session(original.session_id)
        assert [message.content for message in recovered.messages] == [
            "remember this",
            "remembered",
            "continued under P6",
            "P6 continuation",
        ]
    finally:
        application.close()
