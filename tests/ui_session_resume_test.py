from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "new_ui" / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.sessions import SessionStore  # noqa: E402
from models.responses import TaskResponse  # noqa: E402
from services import workflow_service as workflow_service_module  # noqa: E402
from services.workflow_service import WorkflowService  # noqa: E402


def test_task_response_exposes_session_identifiers():
    response = TaskResponse(
        task_id="12345678-aaaa-bbbb-cccc-123456789abc",
        session_id="sess-1",
        task_short_id="12345678",
        status="started",
        message="started",
    )

    payload = response.model_dump()

    assert payload["session_id"] == "sess-1"
    assert payload["task_short_id"] == "12345678"


def test_workflow_service_get_task_by_full_or_short_id():
    service = WorkflowService()
    task = service.create_task(session_id="sess-1", task_kind="chat")
    task.task_short_id = task.task_id[:8]

    assert service.get_task_by_any_id(task.task_id) is task
    assert service.get_task_by_any_id(task.task_id[:8]) is task


def test_hydrate_marks_running_session_tasks_interrupted(tmp_path, monkeypatch):
    store = SessionStore(root=tmp_path / "sessions")
    session = store.create_session(title="resume me")
    store.attach_task(
        session.session_id,
        "deadbeef",
        task_kind="paper",
        task_dir=str(tmp_path / "task"),
        status="running",
    )
    monkeypatch.setattr(workflow_service_module, "session_store", store)

    service = WorkflowService()
    restored = service.hydrate_from_sessions()
    task = service.get_task_by_any_id("deadbeef")
    reloaded = store.get_session(session.session_id)

    assert restored == 1
    assert task is not None
    assert task.status == "interrupted"
    assert reloaded is not None
    assert reloaded.tasks[0].status == "interrupted"
    assert reloaded.tasks[0].metadata["interrupted"] is True
