from pathlib import Path

from core.application import DeepCodeApplication
from core.domain import ThreadMode
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.workflow_repository import WorkflowRepository
from core.sessions import SessionStore


def test_legacy_import_is_read_only_durable_and_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    legacy_root = tmp_path / "legacy"
    store = SessionStore(legacy_root, use_index=False)
    session = store.create_session(
        title="Legacy work",
        metadata={"workspace": str(workspace)},
    )
    store.append_message(session.session_id, "user", "Implement this")
    store.append_message(session.session_id, "assistant", "Implemented")
    store.attach_task(
        session.session_id,
        "task-1",
        task_kind="paper",
        task_dir=workspace / "task-1",
        status="completed",
    )
    source_path = legacy_root / session.session_id / "session.jsonl"
    source_before = source_path.read_bytes()

    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    importer = application.legacy_importer(store)
    imported = importer.import_session(session.session_id, project_id=project.id)
    duplicate = importer.import_session(session.session_id, project_id=project.id)

    assert duplicate.id == imported.id
    assert imported.mode is ThreadMode.PAPER
    assert source_path.read_bytes() == source_before

    restarted = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    assert restarted.threads.read(imported.id) == imported
    with restarted.database.read() as connection:
        turns = TurnRepository(connection).list_for_thread(imported.id)
        assert len(turns) == 1
        items = ItemRepository(connection).list_for_turn(turns[0].id)
        assert [item.payload["text"] for item in items] == [
            "Implement this",
            "Implemented",
        ]
        row = connection.execute(
            "SELECT id FROM workflow_runs WHERE thread_id = ?", (imported.id,)
        ).fetchone()
        workflow = WorkflowRepository(connection).get(row["id"])
        assert workflow is not None
        assert workflow.checkpoint["legacyTaskId"] == "task-1"
