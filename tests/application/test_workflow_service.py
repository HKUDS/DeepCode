from __future__ import annotations

import asyncio
import json
import threading
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
from core.domain import RuntimeWorker, TrustState
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.thread import ThreadStatus
from core.domain.turn import Turn, TurnExecutor, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.coordination_repository import RuntimeCoordinationRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
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


class PhaseProfileCapturingRunner(ScriptedWorkflowRunner):
    def __init__(self) -> None:
        super().__init__()
        self.profiles = {}

    async def run(self, request, callbacks) -> WorkflowOutcome:
        runtime = get_runtime()
        self.profiles = {
            phase: runtime.resolve_execution_profile(phase=phase)
            for phase in ("planning", "implementation")
        }
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


def _shared_applications(
    tmp_path: Path,
    owner_runner: ScriptedWorkflowRunner,
) -> tuple[
    DeepCodeApplication,
    DeepCodeApplication,
    ScriptedWorkflowRunner,
    str,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    database_path = tmp_path / "state.sqlite3"
    session_root = tmp_path / "sessions"
    owner = DeepCodeApplication.open(
        database_path,
        workflow_runner=owner_runner,
        session_store=SessionStore(session_root),
        host_surface="workflow-owner",
        run_automation_scheduler=False,
    )
    try:
        project = owner.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = owner.threads.start(project.id, title="Shared Workflow")
        observer_runner = ScriptedWorkflowRunner()
        observer = DeepCodeApplication.open(
            database_path,
            workflow_runner=observer_runner,
            session_store=SessionStore(session_root),
            host_surface="workflow-observer",
            run_automation_scheduler=False,
        )
    except BaseException:
        owner.close()
        raise
    return owner, observer, observer_runner, thread.id


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ScriptedWorkflowRunner()
    application, thread_id, _workspace = _application(tmp_path, runner)
    registry_job_ids: list[str] = []
    registry_start = application.executions.start

    def track_registry_start(job_id, job_factory, **kwargs) -> None:
        registry_job_ids.append(job_id)
        registry_start(job_id, job_factory, **kwargs)

    monkeypatch.setattr(application.executions, "start", track_registry_start)
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
        assert completed.turn.executor is TurnExecutor.WORKFLOW
        assert completed.turn.execution_epoch == 1
        assert completed.turn.execution_owner_id is None
        assert registry_job_ids == [completed.turn.id]
        assert completed.run.id not in registry_job_ids
        assert application.execution_coordinator.active_claims == ()
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


def test_workflow_registry_start_failure_is_terminal_and_releases_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ScriptedWorkflowRunner()
    application, thread_id, _workspace = _application(tmp_path, runner)

    def reject_start(*_args, **_kwargs) -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(application.executions, "start", reject_start)
    try:
        started = application.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Fail admission cleanly",
        )
        failed = _wait_for(application, started.run.id, WorkflowStatus.FAILED)

        assert failed.run.error_code == "SCHEDULER_ERROR"
        assert failed.turn.status is TurnStatus.FAILED
        assert failed.turn.execution_owner_id is None
        assert application.execution_coordinator.active_claims == ()
        assert runner.requests == []
        with application.database.read() as connection:
            held = RuntimeCoordinationRepository(
                connection
            ).list_held_resources_for_worker(
                application.execution_coordinator.worker_id
            )
        assert held == []
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


def test_workflow_freezes_session_model_for_every_paper2code_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "providers": {
                    "profiles": {
                        "session-provider": {
                            "label": "Session provider",
                            "template": "custom",
                            "apiBase": "https://example.invalid/v1",
                            "modelCatalog": "manual",
                            "manualModels": ["openai/gpt-5.2"],
                        }
                    }
                },
                "agents": {
                    "defaults": {"model": "fallback/default-model"},
                    "planning": {"model": "fallback/planning-model"},
                    "implementation": {"model": "fallback/implementation-model"},
                },
            }
        ),
        encoding="utf-8",
    )
    runner = PhaseProfileCapturingRunner()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        workflow_runner=runner,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(
        project.id,
        title="Session-selected Paper2Code",
        connection_id="session-provider",
        model="openai/gpt-5.2",
        reasoning_effort="high",
        context_window=64_000,
    )
    try:
        started = application.workflows.start(
            thread.id,
            kind="paper2code",
            source_type="requirement",
            source="Implement a small verified feature",
            options={"planReview": False},
        )
        completed = _wait_for(application, started.run.id, WorkflowStatus.COMPLETED)

        assert set(runner.profiles) == {"planning", "implementation"}
        for profile in runner.profiles.values():
            assert profile.connection_id == "session-provider"
            assert profile.model_id == "openai/gpt-5.2"
            assert profile.reasoning_effort == "high"
            assert profile.context_window == 64_000
        captured = completed.run.input["executionProfiles"]
        assert captured["planning"] == runner.profiles["planning"].to_dict()
        assert captured["implementation"] == runner.profiles["implementation"].to_dict()
        assert completed.turn.execution_profile == runner.profiles["implementation"]
        assert "apiKey" not in repr(captured)
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


def test_non_owner_application_resolves_durable_workflow_interaction(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True)
    owner, observer, observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Wait for a response from another process",
        )
        waiting = _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        interaction = waiting.run.checkpoint["interaction"]
        assert interaction["workerId"] == waiting.turn.execution_owner_id
        assert interaction["turnEpoch"] == waiting.turn.execution_epoch

        # Polling is an unbounded reconciliation cadence, not a user timeout.
        time.sleep(0.45)
        assert (
            observer.workflows.read(started.run.id).run.status is WorkflowStatus.WAITING
        )
        with owner.workflows._interaction_lock:
            assert owner.workflows._interactions.pop(started.run.id, None) is not None
        observer.workflows.respond(
            started.run.id,
            interaction_id=interaction["id"],
            response={"decision": "approve", "source": "remote"},
        )

        completed = _wait_for(
            owner,
            started.run.id,
            WorkflowStatus.COMPLETED,
        )
        assert owner_runner.responses == [{"decision": "approve", "source": "remote"}]
        assert observer_runner.requests == []
        assert completed.run.checkpoint["lastInteraction"]["id"] == interaction["id"]
        assert "interaction" not in completed.run.checkpoint
    finally:
        observer.close()
        owner.close()


def test_interaction_response_cas_rejects_stale_and_duplicate_responses(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True, hang=True)
    owner, observer, _observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Accept exactly one response",
        )
        waiting = _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        interaction_id = str(waiting.run.checkpoint["interaction"]["id"])

        with pytest.raises(WorkflowInteractionError) as stale:
            observer.workflows.respond(
                started.run.id,
                interaction_id="wfi_stale",
                response={"decision": "reject"},
            )
        assert stale.value.details["reason"] == "stale"

        barrier = threading.Barrier(2)
        resolved: list[WorkflowRun] = []
        errors: list[WorkflowInteractionError] = []

        def resolve(application: DeepCodeApplication, source: str) -> None:
            barrier.wait()
            try:
                resolved.append(
                    application.workflows.respond(
                        started.run.id,
                        interaction_id=interaction_id,
                        response={"decision": "approve", "source": source},
                    )
                )
            except WorkflowInteractionError as exc:
                errors.append(exc)

        local = threading.Thread(target=resolve, args=(owner, "owner"))
        remote = threading.Thread(target=resolve, args=(observer, "observer"))
        local.start()
        remote.start()
        local.join(timeout=5.0)
        remote.join(timeout=5.0)

        assert not local.is_alive()
        assert not remote.is_alive()
        assert len(resolved) == 1
        assert len(errors) == 1
        assert errors[0].details["reason"] == "already_resolved"
        deadline = time.monotonic() + 5.0
        while not owner_runner.responses and time.monotonic() < deadline:
            time.sleep(0.01)
        assert owner_runner.responses == [
            resolved[0].checkpoint["lastInteraction"]["response"]
        ]
        assert owner.workflows.interrupt(started.run.id)[0] is True
        _wait_for(owner, started.run.id, WorkflowStatus.CANCELLED)
    finally:
        observer.close()
        owner.close()


def test_durable_workflow_cancel_wins_response_race_without_failure(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True, hang=True)
    owner, observer, _observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Cancel while responding",
        )
        waiting = _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        interaction_id = str(waiting.run.checkpoint["interaction"]["id"])
        barrier = threading.Barrier(2)
        cancel_results: list[bool] = []
        response_errors: list[WorkflowInteractionError] = []

        def cancel() -> None:
            barrier.wait()
            cancel_results.append(observer.workflows.interrupt(started.run.id)[0])

        def respond() -> None:
            barrier.wait()
            try:
                observer.workflows.respond(
                    started.run.id,
                    interaction_id=interaction_id,
                    response={"decision": "approve"},
                )
            except WorkflowInteractionError as exc:
                response_errors.append(exc)

        cancelling = threading.Thread(target=cancel)
        responding = threading.Thread(target=respond)
        cancelling.start()
        responding.start()
        cancelling.join(timeout=5.0)
        responding.join(timeout=5.0)

        assert not cancelling.is_alive()
        assert not responding.is_alive()
        assert cancel_results == [True]
        cancelled = _wait_for(
            owner,
            started.run.id,
            WorkflowStatus.CANCELLED,
        )
        assert cancelled.run.error_code is None
        assert cancelled.turn.status is TurnStatus.INTERRUPTED
        assert cancelled.turn.execution_owner_id is None
        if response_errors:
            assert response_errors[0].details["reason"] in {
                "cancel_requested",
                "terminal",
            }

        with pytest.raises(WorkflowInteractionError) as expired:
            observer.workflows.respond(
                started.run.id,
                interaction_id=interaction_id,
                response={"decision": "approve"},
            )
        assert expired.value.details["reason"] == "terminal"
    finally:
        observer.close()
        owner.close()


def test_persisted_cancel_request_rejects_late_interaction_response(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True, hang=True)
    owner, observer, _observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Cancel before a late response",
        )
        waiting = _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        interaction_id = str(waiting.run.checkpoint["interaction"]["id"])

        requested = observer.workflows._request_cancellation(waiting.turn.id)
        assert requested.cancel_requested_at is not None
        with pytest.raises(WorkflowInteractionError) as rejected:
            observer.workflows.respond(
                started.run.id,
                interaction_id=interaction_id,
                response={"decision": "approve"},
            )
        assert rejected.value.details["reason"] == "cancel_requested"

        accepted, current = observer.workflows.interrupt(started.run.id)
        # The durable request above is visible to the owning worker. It may
        # finish cancellation before this process reaches ``interrupt``; a
        # terminal Workflow correctly reports that there is nothing left to
        # interrupt.
        assert accepted is True or current.status is WorkflowStatus.CANCELLED
        cancelled = _wait_for(
            owner,
            started.run.id,
            WorkflowStatus.CANCELLED,
        )
        assert cancelled.turn.status is TurnStatus.INTERRUPTED
        assert owner_runner.responses == []
    finally:
        observer.close()
        owner.close()


def test_interaction_response_and_projection_updates_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True)
    owner, observer, _observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Keep response projection atomic",
        )
        waiting = _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        interaction_id = str(waiting.run.checkpoint["interaction"]["id"])
        original_update = ItemRepository.update

        def reject_item_update(_repository, _item) -> None:
            raise RuntimeError("simulated interaction item failure")

        monkeypatch.setattr(ItemRepository, "update", reject_item_update)
        with pytest.raises(
            RuntimeError,
            match="simulated interaction item failure",
        ):
            observer.workflows.respond(
                started.run.id,
                interaction_id=interaction_id,
                response={"decision": "approve"},
            )

        rolled_back = owner.workflows.read(started.run.id)
        assert rolled_back.run.status is WorkflowStatus.WAITING
        assert rolled_back.run.checkpoint["interaction"]["id"] == interaction_id
        plan_item = next(
            item for item in rolled_back.items if item.kind is ItemKind.PLAN
        )
        assert plan_item.status is ItemStatus.PENDING

        monkeypatch.setattr(ItemRepository, "update", original_update)
        observer.workflows.respond(
            started.run.id,
            interaction_id=interaction_id,
            response={"decision": "approve"},
        )
        _wait_for(owner, started.run.id, WorkflowStatus.COMPLETED)
    finally:
        observer.close()
        owner.close()


def test_closing_interaction_owner_cancels_without_reporting_failure(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(interaction=True, hang=True)
    owner, observer, _observer_runner, thread_id = _shared_applications(
        tmp_path,
        owner_runner,
    )
    owner_closed = False
    try:
        started = owner.workflows.start(
            thread_id,
            kind="paper2code",
            source_type="requirement",
            source="Close cleanly while waiting",
        )
        _wait_for(owner, started.run.id, WorkflowStatus.WAITING)
        owner.close()
        owner_closed = True

        closed = observer.workflows.read(started.run.id)
        assert closed.run.status is WorkflowStatus.CANCELLED
        assert closed.run.error_code is None
        assert closed.turn.status is TurnStatus.INTERRUPTED
        assert closed.turn.execution_owner_id is None
    finally:
        observer.close()
        if not owner_closed:
            owner.close()


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


def test_remote_process_requests_workflow_cancel_from_claim_owner(
    tmp_path: Path,
) -> None:
    owner_runner = ScriptedWorkflowRunner(hang=True)
    owner, thread_id, _workspace = _application(tmp_path, owner_runner)
    started = owner.workflows.start(
        thread_id,
        kind="paper2code",
        source_type="requirement",
        source="Wait for a durable remote cancellation",
    )
    _wait_for(owner, started.run.id, WorkflowStatus.RUNNING)
    observer_runner = ScriptedWorkflowRunner(hang=True)
    observer = DeepCodeApplication.open(
        owner.database.path,
        workflow_runner=observer_runner,
        session_store=owner.session_store,
    )
    try:
        accepted, _active = observer.workflows.interrupt(started.run.id)
        assert accepted is True
        cancelled = _wait_for(
            observer,
            started.run.id,
            WorkflowStatus.CANCELLED,
        )

        assert cancelled.turn.status is TurnStatus.INTERRUPTED
        assert cancelled.turn.cancel_requested_at is not None
        assert cancelled.turn.execution_owner_id is None
        assert owner.execution_coordinator.active_claims == ()
        assert len(owner_runner.requests) == 1
        assert observer_runner.requests == []
    finally:
        observer.close()
        owner.close()


def test_dead_worker_workflow_is_failed_once_and_never_replayed(
    tmp_path: Path,
) -> None:
    runner = ScriptedWorkflowRunner()
    application, thread_id, _workspace = _application(tmp_path, runner)
    application.execution_coordinator.quiesce()
    thread = application.threads.read(thread_id)
    now = datetime.now(timezone.utc)
    dead = RuntimeWorker(
        id="worker_dead_workflow",
        pid=4242,
        surface="test",
        started_at=now,
        heartbeat_at=now,
    )
    turn = Turn(
        thread_id=thread_id,
        ordinal=1,
        prompt="Do not replay this Workflow",
        executor=TurnExecutor.WORKFLOW,
        home_worker_id=dead.id,
        enqueued_at=now,
    )
    run = WorkflowRun(
        thread_id=thread_id,
        turn_id=turn.id,
        kind="paper2code",
        status=WorkflowStatus.RUNNING,
        input={
            "sourceType": "requirement",
            "source": "Recover without replay",
            "options": {},
        },
        checkpoint={"taskId": "dead-worker-task"},
        started_at=now,
    )
    with application.database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(dead)
        turns = TurnRepository(connection)
        turns.add(turn)
        WorkflowRepository(connection).add(run)
        claim = coordination.claim_turn_resources(
            dead.id,
            turn.id,
            (
                "capacity:global:turn:0",
                f"thread:{thread_id}",
                f"workspace:project:{thread.project_id}:canonical",
            ),
            acquired_at=now,
        )
        assert claim is not None
        claimed = turns.get(turn.id)
        assert claimed is not None
        turns.update(
            replace(
                claimed,
                status=TurnStatus.RUNNING,
                started_at=now,
            )
        )

    try:
        recovery = application.execution_coordinator.recover_dead_worker(dead.id)
        assert recovery is not None
        assert [entry.turn_id for entry in recovery.requires_settlement] == [turn.id]
        recovered = application.workflows.read(run.id)
        assert recovered.run.status is WorkflowStatus.FAILED
        assert recovered.run.error_code == "WORKFLOW_INTERRUPTED"
        assert recovered.run.checkpoint["resumable"] is True
        assert recovered.turn.status is TurnStatus.INTERRUPTED
        assert recovered.turn.stop_reason == "worker_crashed"
        assert recovered.turn.execution_owner_id is None
        assert runner.requests == []
        with application.database.read() as connection:
            assert not RuntimeCoordinationRepository(connection).claim_is_current(claim)
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
        threads.update(replace(thread, status=ThreadStatus.WAITING, updated_at=now))
        turns = TurnRepository(connection)
        turn = Turn(
            thread_id=thread_id,
            ordinal=turns.next_ordinal(thread_id),
            prompt="Recover me",
            executor=TurnExecutor.WORKFLOW,
            status=TurnStatus.WAITING_APPROVAL,
            started_at=now,
        )
        turns.add(turn)
        item = Item(
            thread_id=thread_id,
            turn_id=turn.id,
            ordinal=1,
            kind=ItemKind.PLAN,
            status=ItemStatus.PENDING,
            summary="Interrupted review",
            payload={"interactionId": "wfi_recovery"},
            created_at=now,
            updated_at=now,
        )
        ItemRepository(connection).add(item)
        WorkflowRepository(connection).add(
            WorkflowRun(
                thread_id=thread_id,
                turn_id=turn.id,
                kind="paper2code",
                status=WorkflowStatus.WAITING,
                input={
                    "sourceType": "requirement",
                    "source": "Recover",
                    "options": {},
                },
                checkpoint={
                    "taskId": "preserved",
                    "interaction": {
                        "id": "wfi_recovery",
                        "itemId": item.id,
                        "request": {"message": "Review"},
                    },
                },
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
        plan_item = next(current for current in snapshot.items if current.id == item.id)
        assert plan_item.status is ItemStatus.FAILED
        with pytest.raises(WorkflowInteractionError) as expired:
            recovered.workflows.respond(
                run.id,
                interaction_id="wfi_recovery",
                response={"decision": "approve"},
            )
        assert expired.value.details["reason"] == "terminal"
    finally:
        recovered.close()


def test_legacy_human_success_text_is_not_a_completion_signal(tmp_path: Path) -> None:
    result = _normalize_result("Successfully generated files", tmp_path, "task")
    assert result["status"] == "incomplete"
