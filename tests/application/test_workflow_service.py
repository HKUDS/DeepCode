from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import WorkflowInteractionError
from core.application.workflow_adapter import (
    ArtifactSpec,
    WorkflowExecutionRequest,
    WorkflowOutcome,
    _normalize_result,
)
from core.compat.runtime import get_runtime
from core.domain import TrustState
from core.domain.item import ItemKind, ItemStatus
from core.domain.thread import ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.execution_repository import TurnRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import WorkflowRepository
from core.sessions import SessionStore


class ScriptedWorkflowRunner:
    def __init__(
        self,
        *,
        outcomes: tuple[str, ...] = ("completed",),
        interaction: bool = False,
        hang: bool = False,
        outside_artifact: Path | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.interaction = interaction
        self.hang = hang
        self.outside_artifact = outside_artifact
        self.requests: list[WorkflowExecutionRequest] = []
        self.responses: list[dict] = []

    async def run(self, request, callbacks) -> WorkflowOutcome:
        self.requests.append(request)
        await callbacks.progress(
            "planning", 40, 100, "Planning implementation", {"taskId": request.run_id}
        )
        if self.interaction:
            response = await callbacks.interact(
                {
                    "type": "plan_review",
                    "message": "Approve the implementation plan",
                    "plan": ["parse", "implement", "test"],
                }
            )
            self.responses.append(response)
        if self.hang:
            await asyncio.Event().wait()
        await callbacks.progress(
            "testing", 95, 100, "Running verification", {"taskId": request.run_id}
        )
        output = request.workspace / ".deepcode" / "workflows" / "result.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("verified output\n", encoding="utf-8")
        artifact_path = self.outside_artifact or output
        index = min(len(self.requests) - 1, len(self.outcomes) - 1)
        status = self.outcomes[index]
        return WorkflowOutcome(
            status=status,
            summary="verified" if status == "completed" else "tests did not pass",
            result={"status": status, "testsPassed": status == "completed"},
            artifacts=(
                ArtifactSpec("report", "result.md", "text/markdown", artifact_path),
            ),
        )


class RuntimeCapturingRunner(ScriptedWorkflowRunner):
    def __init__(self) -> None:
        super().__init__()
        self.models: list[str] = []

    async def run(self, request, callbacks) -> WorkflowOutcome:
        self.models.append(get_runtime().config.agents.defaults.model)
        return await super().run(request, callbacks)


class RejectTaskStore(SessionStore):
    def attach_task(self, *args, **kwargs):
        return None


def _application(
    tmp_path: Path, runner: ScriptedWorkflowRunner
) -> tuple[DeepCodeApplication, str, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", workflow_runner=runner
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Paper2Code")
    return application, thread.id, workspace


def _wait_for(application: DeepCodeApplication, run_id: str, status: WorkflowStatus):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = application.workflows.read(run_id)
        if snapshot.run.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"workflow did not reach {status}: {application.workflows.read(run_id).run}"
    )


def test_completed_workflow_projects_progress_and_bounded_artifact(
    tmp_path: Path,
) -> None:
    runner = ScriptedWorkflowRunner()
    application, thread_id, _workspace = _application(tmp_path, runner)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Implement the paper faithfully",
            options={"planReview": False},
        )
        completed = _wait_for(application, started.run.id, WorkflowStatus.COMPLETED)

        assert completed.turn.status is TurnStatus.COMPLETED
        assert completed.run.result == {"status": "completed", "testsPassed": True}
        assert completed.run.progress_current == completed.run.progress_total == 100
        assert [artifact.storage_path for artifact in completed.artifacts] == [
            ".deepcode/workflows/result.md"
        ]
        preview = application.workflows.read_artifact(completed.artifacts[0].id)
        assert preview.content == "verified output\n"
        assert preview.truncated is False
        assert any(item.kind is ItemKind.WORKFLOW_STAGE for item in completed.items)
        assert all(
            item.status not in {ItemStatus.PENDING, ItemStatus.IN_PROGRESS}
            for item in completed.items
        )
        assert application.threads.read(thread_id).status is ThreadStatus.IDLE
        session = application.session_store.get_session(thread_id)
        assert session is not None
        assert [message.role for message in session.messages] == ["user", "assistant"]
        assert session.messages[0].metadata["workflowRunId"] == completed.run.id
        assert session.messages[1].metadata["workflowRunId"] == completed.run.id
        assert len(session.tasks) == 1
        assert session.tasks[0].status == "completed"
        assert session.tasks[0].metadata["workflowRunId"] == completed.run.id
    finally:
        application.close()


def test_workflow_uses_selected_workspace_config_not_server_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    unrelated = tmp_path / "server-cwd"
    home.mkdir()
    workspace.mkdir()
    unrelated.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    (home / "deepcode_config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "openai/user"}}}),
        encoding="utf-8",
    )
    (workspace / "deepcode_config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "openai/project"}}}),
        encoding="utf-8",
    )
    (unrelated / "deepcode_config.json").write_text(
        json.dumps({"agents": {"defaults": {"model": "openai/wrong-cwd"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(unrelated)
    runner = RuntimeCapturingRunner()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        workflow_runner=runner,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Workspace config")
    try:
        started = application.workflows.start(
            thread.id,
            kind="paper2code",
            source_type="requirement",
            source="Use the selected project configuration",
            options={"planReview": False},
        )
        _wait_for(application, started.run.id, WorkflowStatus.COMPLETED)
        assert runner.models == ["openai/project"]
    finally:
        application.close()


def test_workflow_session_persistence_failure_settles_durable_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = RejectTaskStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_store=store,
        workflow_runner=ScriptedWorkflowRunner(),
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(project.id, title="Persistence failure")
    try:
        with pytest.raises(
            RuntimeError,
            match="could not attach workflow to canonical session",
        ):
            application.workflows.start(
                thread.id,
                kind="paper2code",
                source_type="requirement",
                source="Fail before scheduling",
            )
        runs = application.workflows.list_for_thread(thread.id)
        assert len(runs) == 1
        snapshot = application.workflows.read(runs[0].id)
        assert snapshot.run.status is WorkflowStatus.FAILED
        assert snapshot.run.error_code == "SESSION_PERSISTENCE_ERROR"
        assert snapshot.turn.status is TurnStatus.FAILED
        assert application.threads.read(thread.id).status is ThreadStatus.FAILED
    finally:
        application.close()


def test_plan_review_waits_and_rejects_stale_interaction(tmp_path: Path) -> None:
    runner = ScriptedWorkflowRunner(interaction=True)
    application, thread_id, _workspace = _application(tmp_path, runner)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Build it",
            options={"planReview": True},
        )
        waiting = _wait_for(application, started.run.id, WorkflowStatus.WAITING)
        interaction = waiting.run.checkpoint["interaction"]
        assert waiting.turn.status is TurnStatus.WAITING_APPROVAL
        assert application.threads.read(thread_id).status is ThreadStatus.WAITING
        asyncio.run(
            application.workflows._progress(
                started.run.id,
                stage="late_progress",
                current=70,
                total=100,
                message="Must not clear the review gate",
                metadata={},
            )
        )
        assert (
            application.workflows.read(started.run.id).run.status
            is WorkflowStatus.WAITING
        )

        with pytest.raises(WorkflowInteractionError):
            application.workflows.respond(
                started.run.id,
                interaction_id="wfi_stale",
                response={"decision": "approve"},
            )
        application.workflows.respond(
            started.run.id,
            interaction_id=interaction["id"],
            response={"decision": "approve", "feedback": "Proceed"},
        )
        completed = _wait_for(application, started.run.id, WorkflowStatus.COMPLETED)
        assert runner.responses == [{"decision": "approve", "feedback": "Proceed"}]
        plan_item = next(item for item in completed.items if item.kind is ItemKind.PLAN)
        assert plan_item.status is ItemStatus.COMPLETED
        assert plan_item.payload["response"]["decision"] == "approve"
    finally:
        application.close()


def test_incomplete_workflow_fails_truthfully_and_retry_reuses_checkpoint(
    tmp_path: Path,
) -> None:
    runner = ScriptedWorkflowRunner(outcomes=("incomplete", "completed"))
    application, thread_id, _workspace = _application(tmp_path, runner)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Build and verify",
        )
        failed = _wait_for(application, started.run.id, WorkflowStatus.FAILED)
        assert failed.run.error_code == "WORKFLOW_INCOMPLETE"
        assert failed.turn.status is TurnStatus.FAILED
        assert failed.run.checkpoint["resumable"] is True

        retried = application.workflows.retry(failed.run.id)
        completed = _wait_for(application, retried.run.id, WorkflowStatus.COMPLETED)
        assert completed.run.attempt == 2
        assert completed.run.retry_of == failed.run.id
        assert (
            runner.requests[0].checkpoint["taskId"]
            == runner.requests[1].checkpoint["taskId"]
        )
    finally:
        application.close()


def test_interrupt_cancels_runner_and_artifacts_cannot_escape_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    escaping = ScriptedWorkflowRunner(outside_artifact=outside)
    application, thread_id, _workspace = _application(tmp_path, escaping)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Do not leak artifacts",
        )
        completed = _wait_for(application, started.run.id, WorkflowStatus.COMPLETED)
        assert completed.artifacts == ()
    finally:
        application.close()

    hanging = ScriptedWorkflowRunner(hang=True)
    application, thread_id, _workspace = _application(tmp_path / "interrupt", hanging)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Wait",
        )
        _wait_for(application, started.run.id, WorkflowStatus.RUNNING)
        assert application.workflows.interrupt(started.run.id)[0] is True
        cancelled = _wait_for(application, started.run.id, WorkflowStatus.CANCELLED)
        assert cancelled.turn.status is TurnStatus.INTERRUPTED
    finally:
        application.close()


def test_open_recovers_workflow_before_generic_turn_recovery(tmp_path: Path) -> None:
    runner = ScriptedWorkflowRunner()
    first, thread_id, _workspace = _application(tmp_path, runner)
    database_path = first.database.path
    first.close()
    now = datetime.now(timezone.utc)
    with first.database.transaction() as connection:
        threads = ThreadRepository(connection)
        thread = threads.get(thread_id)
        assert thread is not None
        threads.update(replace(thread, status=ThreadStatus.RUNNING, updated_at=now))
        turns = TurnRepository(connection)
        turn = Turn(
            thread_id=thread_id,
            ordinal=turns.next_ordinal(thread_id),
            prompt="Recover me",
            status=TurnStatus.RUNNING,
            started_at=now,
        )
        turns.add(turn)
        WorkflowRepository(connection).add(
            WorkflowRun(
                thread_id=thread_id,
                turn_id=turn.id,
                kind="paper2code",
                status=WorkflowStatus.RUNNING,
                input={
                    "sourceType": "requirement",
                    "source": "Recover",
                    "options": {},
                },
                checkpoint={"taskId": "preserved"},
                started_at=now,
            )
        )

    recovered = DeepCodeApplication.open(database_path, workflow_runner=runner)
    try:
        run = recovered.workflows.list_for_thread(thread_id)[0]
        snapshot = recovered.workflows.read(run.id)
        assert snapshot.run.status is WorkflowStatus.FAILED
        assert snapshot.run.error_code == "WORKFLOW_INTERRUPTED"
        assert snapshot.run.checkpoint == {"taskId": "preserved", "resumable": True}
        assert snapshot.turn.status is TurnStatus.INTERRUPTED
        assert snapshot.turn.stop_reason == "application_restarted"
    finally:
        recovered.close()


def test_legacy_human_success_text_is_not_a_completion_signal(tmp_path: Path) -> None:
    result = _normalize_result("Successfully generated files", tmp_path, "task")
    assert result["status"] == "incomplete"
