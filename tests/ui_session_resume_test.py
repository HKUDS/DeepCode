from __future__ import annotations

import asyncio
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


def test_workflow_error_details_classify_timeout_and_include_logs(tmp_path, monkeypatch):
    store = SessionStore(root=tmp_path / "sessions")
    session = store.create_session(title="debug errors")
    task_dir = tmp_path / "deepcode_lab" / "tasks" / "paper_deadbeef"
    store.attach_task(
        session.session_id,
        "deadbeef",
        task_kind="paper",
        task_dir=str(task_dir),
        status="running",
    )
    monkeypatch.setattr(workflow_service_module, "session_store", store)

    service = WorkflowService()
    task = service.create_task(session_id=session.session_id, task_kind="paper")
    task.task_short_id = "deadbeef"
    details = service._build_error_details(
        task,
        RuntimeError(
            "Workflow execution failed: <html><h1>504 Gateway Time-out</h1></html> api_key=sk-secret123456"
        ),
        stage="Planning",
        progress=65,
    )

    assert details["category"] == "provider_timeout"
    assert details["stage"] == "Planning"
    assert details["progress"] == 65
    assert details["task_short_id"] == "deadbeef"
    assert details["task_dir"] == str(task_dir)
    assert details["log_stream_url"] == "/ws/tasks/deadbeef/logs"
    assert Path(details["log_paths"]["llm"]).name == "llm.jsonl"
    assert "sk-secret" not in details["message"]
    assert "provider" in details["hint"].lower()


def test_workflow_error_payload_preserves_legacy_error_string():
    service = WorkflowService()
    task = service.create_task(session_id="sess-1", task_kind="chat")
    task.task_short_id = "cafebabe"

    service._mark_task_error(task, ValueError("LLM provider returned 429 rate limit"))
    payload = service._error_payload(task)

    assert task.status == "error"
    assert payload["type"] == "error"
    assert payload["error"] == "LLM provider returned 429 rate limit"
    assert payload["error_details"]["category"] == "rate_limit"


def test_pipeline_error_result_broadcasts_error_message(monkeypatch):
    service = WorkflowService()
    task = service.create_task(session_id="sess-1", task_kind="paper")
    messages = []

    async def fake_broadcast(task_id, message):
        messages.append((task_id, message))

    monkeypatch.setattr(service, "_broadcast", fake_broadcast)

    result = asyncio.run(
        service._finish_task_with_pipeline_result(
            task.task_id,
            task,
            {
                "status": "error",
                "summary": "Pipeline failed after planning",
                "implementation": {
                    "status": "error",
                    "message": "File tree structure not found",
                },
            },
        )
    )

    assert result["status"] == "error"
    assert task.status == "error"
    assert task.error == "File tree structure not found"
    assert messages[0][1]["type"] == "error"
    assert messages[0][1]["error_details"]["result_status"] == "error"
