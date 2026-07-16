"""Durable Paper2Code orchestration shared by every product frontend."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.application.errors import (
    ArtifactNotFoundError,
    ConflictError,
    InvalidArgumentError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
    TurnAlreadyRunningError,
    WorkflowInteractionError,
    WorkflowNotFoundError,
)
from core.application.event_service import EventBroker
from core.application.execution_registry import ExecutionRegistry
from core.application.views import (
    artifact_view,
    item_view,
    thread_view,
    turn_view,
    workflow_view,
)
from core.application.workflow_adapter import (
    DefaultWorkflowRunner,
    WorkflowCallbacks,
    WorkflowExecutionRequest,
    WorkflowOutcome,
    WorkflowRunner,
)
from core.application.workspace_service import WorkspaceService
from core.compat.runtime import DeepCodeRuntime, use_runtime
from core.config import load_config_for_workspace
from core.domain.artifact import Artifact
from core.domain.common import JsonObject, new_id, utc_now, validate_json_object
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.project import TrustState
from core.domain.thread import ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.persistence.workflow_repository import ArtifactRepository, WorkflowRepository
from core.sessions import SessionStore


SUPPORTED_KINDS = frozenset({"paper2code"})
SUPPORTED_SOURCE_TYPES = frozenset({"local", "url", "repository", "requirement"})
MAX_SOURCE_LENGTH = 16_384
MAX_SUMMARY_LENGTH = 4_000
MAX_PREVIEW_BYTES = 128 * 1024


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    run: WorkflowRun
    turn: Turn
    items: tuple[Item, ...]
    artifacts: tuple[Artifact, ...]


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    artifact: Artifact
    content: str | None
    truncated: bool
    directory: bool


@dataclass(slots=True)
class _InteractionWaiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[JsonObject]


class WorkflowService:
    """Own workflow lifecycle, checkpoints, interactions, and artifacts."""

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        workspaces: WorkspaceService,
        registry: ExecutionRegistry,
        *,
        session_store: SessionStore,
        runner: WorkflowRunner | None = None,
    ) -> None:
        self.database = database
        self.broker = broker
        self.workspaces = workspaces
        self.registry = registry
        self.session_store = session_store
        self.runner = runner or DefaultWorkflowRunner()
        self._interaction_lock = threading.RLock()
        self._interactions: dict[str, _InteractionWaiter] = {}

    def start(
        self,
        thread_id: str,
        *,
        kind: str,
        source_type: str,
        source: str,
        options: JsonObject | None = None,
    ) -> WorkflowSnapshot:
        return self._start(
            thread_id,
            kind=kind,
            source_type=source_type,
            source=source,
            options=options or {},
        )

    def retry(self, run_id: str) -> WorkflowSnapshot:
        previous = self.read(run_id).run
        if previous.status not in {
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise ConflictError("only failed or cancelled workflows can be retried")
        return self._start(
            previous.thread_id,
            kind=previous.kind,
            source_type=str(previous.input.get("sourceType", "")),
            source=str(previous.input.get("source", "")),
            options=_require_object(previous.input.get("options", {}), "options"),
            retry_of=previous,
        )

    def read(self, run_id: str) -> WorkflowSnapshot:
        with self.database.read() as connection:
            run = WorkflowRepository(connection).get(run_id)
            if run is None:
                raise WorkflowNotFoundError(f"workflow not found: {run_id}")
            turn = TurnRepository(connection).get(run.turn_id)
            if turn is None:
                raise ConflictError("workflow turn is missing")
            items = ItemRepository(connection).list_for_turn(run.turn_id)
            artifacts = ArtifactRepository(connection).list_for_workflow(run.id)
        return WorkflowSnapshot(run, turn, tuple(items), tuple(artifacts))

    def list_for_thread(self, thread_id: str) -> tuple[WorkflowRun, ...]:
        self.workspaces.resolve(thread_id)
        with self.database.read() as connection:
            runs = WorkflowRepository(connection).list_for_thread(thread_id)
        return tuple(runs)

    def list_artifacts(self, thread_id: str) -> tuple[Artifact, ...]:
        self.workspaces.resolve(thread_id)
        with self.database.read() as connection:
            artifacts = ArtifactRepository(connection).list_for_thread(thread_id)
        return tuple(artifacts)

    def read_artifact(
        self, artifact_id: str, *, max_bytes: int = MAX_PREVIEW_BYTES
    ) -> ArtifactContent:
        if not 1 <= max_bytes <= MAX_PREVIEW_BYTES:
            raise InvalidArgumentError(
                f"maxBytes must be between 1 and {MAX_PREVIEW_BYTES}"
            )
        with self.database.read() as connection:
            artifact = ArtifactRepository(connection).get(artifact_id)
        if artifact is None:
            raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
        context = self.workspaces.resolve(artifact.thread_id)
        path = self.workspaces.path(context, artifact.storage_path)
        if path.is_dir():
            return ArtifactContent(artifact, None, False, True)
        if not path.is_file():
            raise ArtifactNotFoundError("artifact is no longer a regular file")
        if not _is_text_artifact(artifact, path):
            return ArtifactContent(artifact, None, False, False)
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        return ArtifactContent(
            artifact,
            raw[:max_bytes].decode("utf-8", errors="replace"),
            truncated,
            False,
        )

    def interrupt(self, run_id: str) -> tuple[bool, WorkflowRun]:
        run = self.read(run_id).run
        if run.status.is_terminal:
            return False, run
        accepted = self.registry.interrupt(run.id)
        if not accepted:
            self._finish(run.id, status=WorkflowStatus.CANCELLED)
        return True, self.read(run_id).run

    def respond(
        self,
        run_id: str,
        *,
        interaction_id: str,
        response: JsonObject,
    ) -> WorkflowRun:
        clean_response = _bounded_json(response, "response")
        events: list[DomainEvent] = []
        with self._interaction_lock:
            waiter = self._interactions.get(run_id)
        if waiter is None:
            raise WorkflowInteractionError("workflow has no live interaction")

        with self.database.transaction() as connection:
            runs = WorkflowRepository(connection)
            run = runs.get(run_id)
            if run is None:
                raise WorkflowNotFoundError(f"workflow not found: {run_id}")
            interaction = run.checkpoint.get("interaction")
            if run.status is not WorkflowStatus.WAITING or not isinstance(
                interaction, dict
            ):
                raise WorkflowInteractionError("workflow is not waiting for input")
            if interaction.get("id") != interaction_id:
                raise WorkflowInteractionError("interaction is stale")
            now = utc_now()
            checkpoint = dict(run.checkpoint)
            checkpoint.pop("interaction", None)
            checkpoint["lastInteraction"] = {
                "id": interaction_id,
                "response": clean_response,
            }
            resumed = replace(
                run,
                status=WorkflowStatus.RUNNING,
                checkpoint=checkpoint,
                updated_at=now,
            )
            runs.update(resumed)
            turns = TurnRepository(connection)
            turn = turns.get(run.turn_id)
            if turn is None:
                raise ConflictError("workflow turn is missing")
            resumed_turn = replace(turn, status=TurnStatus.RUNNING)
            turns.update(resumed_turn)
            threads = ThreadRepository(connection)
            thread = threads.get(run.thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {run.thread_id}")
            resumed_thread = replace(
                thread, status=ThreadStatus.RUNNING, updated_at=now
            )
            threads.update(resumed_thread)
            item_id = interaction.get("itemId")
            items = ItemRepository(connection)
            item = items.get(str(item_id)) if item_id else None
            event_repo = EventRepository(connection)
            if item is not None:
                answered = replace(
                    item,
                    status=ItemStatus.COMPLETED,
                    payload={**item.payload, "response": clean_response},
                    updated_at=now,
                )
                items.update(answered)
                events.append(
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=answered.id,
                        type="item.updated",
                        payload={"item": item_view(answered)},
                    )
                )
            events.extend(
                (
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="workflow.updated",
                        payload={"workflow": workflow_view(resumed)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="turn.updated",
                        payload={"turn": turn_view(resumed_turn)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(resumed_thread)},
                    ),
                )
            )
        self._publish(events)

        def deliver() -> None:
            if not waiter.future.done():
                waiter.future.set_result(clean_response)

        waiter.loop.call_soon_threadsafe(deliver)
        return resumed

    def recover_incomplete(self) -> int:
        """Fail closed on restart while preserving a checkpoint for explicit retry."""

        with self.database.read() as connection:
            run_ids = [
                run.id for run in WorkflowRepository(connection).list_incomplete()
            ]
        for run_id in run_ids:
            self._finish(
                run_id,
                status=WorkflowStatus.FAILED,
                error_code="WORKFLOW_INTERRUPTED",
                error_message="workflow interrupted by application restart",
                checkpoint_patch={"resumable": True},
                turn_status=TurnStatus.INTERRUPTED,
                turn_stop_reason="application_restarted",
            )
        return len(run_ids)

    def _start(
        self,
        thread_id: str,
        *,
        kind: str,
        source_type: str,
        source: str,
        options: JsonObject,
        retry_of: WorkflowRun | None = None,
    ) -> WorkflowSnapshot:
        clean_kind, clean_source_type, clean_source, clean_options = _validate_input(
            kind, source_type, source, options
        )
        context = self.workspaces.resolve(thread_id, require_trusted=True)
        if clean_source_type == "local":
            try:
                local_path = Path(clean_source).expanduser().resolve(strict=True)
            except OSError as exc:
                raise InvalidArgumentError(
                    "local workflow source does not exist"
                ) from exc
            if not local_path.is_file():
                raise InvalidArgumentError("local workflow source must be a file")
            clean_source = str(local_path)

        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            threads = ThreadRepository(connection)
            thread = threads.get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            project = ProjectRepository(connection).get(thread.project_id)
            if project is None or project.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must remain trusted for workflow execution"
                )
            if thread.status is ThreadStatus.ARCHIVED:
                raise ConflictError("cannot start a workflow in an archived thread")
            turns = TurnRepository(connection)
            active = turns.active_for_thread(thread_id)
            if active is not None:
                raise TurnAlreadyRunningError(
                    f"thread already has an active turn: {active.id}"
                )
            if WorkflowRepository(connection).active_for_thread(thread_id) is not None:
                raise ConflictError("thread already has an active workflow")
            prompt = _workflow_prompt(clean_source_type, clean_source)
            turn = Turn(
                thread_id=thread_id,
                ordinal=turns.next_ordinal(thread_id),
                prompt=prompt,
            )
            turns.add(turn)
            runs = WorkflowRepository(connection)
            run = WorkflowRun(
                thread_id=thread_id,
                turn_id=turn.id,
                kind=clean_kind,
                input={
                    "sourceType": clean_source_type,
                    "source": clean_source,
                    "options": clean_options,
                },
                attempt=(retry_of.attempt + 1) if retry_of else 1,
                retry_of=retry_of.id if retry_of else None,
                checkpoint=dict(retry_of.checkpoint) if retry_of else {},
            )
            checkpoint = dict(run.checkpoint)
            checkpoint["taskId"] = str(checkpoint.get("taskId") or run.id)
            checkpoint.pop("interaction", None)
            checkpoint.pop("resumable", None)
            run = replace(run, checkpoint=checkpoint)
            runs.add(run)
            now = utc_now()
            items = ItemRepository(connection)
            user_item = Item(
                thread_id=thread_id,
                turn_id=turn.id,
                ordinal=items.next_ordinal(turn.id),
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=prompt[:160],
                payload={"text": prompt, "workflowRunId": run.id},
                created_at=now,
                updated_at=now,
            )
            items.add(user_item)
            running_thread = replace(
                thread, status=ThreadStatus.RUNNING, updated_at=now
            )
            threads.update(running_thread)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn.id,
                        type="turn.started",
                        payload={"turn": turn_view(turn)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn.id,
                        item_id=user_item.id,
                        type="item.created",
                        payload={"item": item_view(user_item)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn.id,
                        type="workflow.started",
                        payload={"workflow": workflow_view(run)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(running_thread)},
                    ),
                )
            )
        self._publish(events)
        try:
            self._persist_workflow_start(run, prompt, context.root)
        except Exception as exc:
            self._finish(
                run.id,
                status=WorkflowStatus.FAILED,
                error_code="SESSION_PERSISTENCE_ERROR",
                error_message=str(exc),
            )
            raise
        try:
            self.registry.start(
                run.id,
                lambda: self._execute(run.id, context.root),
                on_cancelled_before_start=lambda: self._finish(
                    run.id, status=WorkflowStatus.CANCELLED
                ),
            )
        except Exception as exc:
            self._finish(
                run.id,
                status=WorkflowStatus.FAILED,
                error_code="SCHEDULER_ERROR",
                error_message=str(exc),
            )
            raise
        return WorkflowSnapshot(run, turn, (user_item,), ())

    async def _execute(self, run_id: str, workspace: Path) -> None:
        run = self._mark_running(run_id)
        source_type = str(run.input["sourceType"])
        source = str(run.input["source"])
        options = _require_object(run.input.get("options", {}), "options")
        request = WorkflowExecutionRequest(
            run_id=run.id,
            kind=run.kind,
            source_type=source_type,
            source=source,
            options=options,
            workspace=workspace,
            checkpoint=run.checkpoint,
        )
        try:
            runtime = DeepCodeRuntime(load_config_for_workspace(workspace))
            with use_runtime(runtime):
                outcome = await self.runner.run(
                    request,
                    WorkflowCallbacks(
                        progress=lambda stage, current, total, message, metadata: (
                            self._progress(
                                run.id,
                                stage=stage,
                                current=current,
                                total=total,
                                message=message,
                                metadata=metadata,
                            )
                        ),
                        interact=lambda interaction: self._interact(
                            run.id, interaction
                        ),
                    ),
                )
            if outcome.status == "completed":
                self._finish(
                    run.id,
                    status=WorkflowStatus.COMPLETED,
                    outcome=outcome,
                )
            elif outcome.status == "cancelled":
                self._finish(
                    run.id,
                    status=WorkflowStatus.CANCELLED,
                    outcome=outcome,
                )
            else:
                self._finish(
                    run.id,
                    status=WorkflowStatus.FAILED,
                    outcome=outcome,
                    error_code="WORKFLOW_INCOMPLETE",
                    error_message=outcome.summary,
                    checkpoint_patch={"resumable": True},
                )
        except asyncio.CancelledError:
            self._finish(run.id, status=WorkflowStatus.CANCELLED)
            raise
        except Exception as exc:  # noqa: BLE001 - durable failure boundary
            self._finish(
                run.id,
                status=WorkflowStatus.FAILED,
                error_code="WORKFLOW_EXECUTION_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                checkpoint_patch={"resumable": True},
            )
        finally:
            with self._interaction_lock:
                self._interactions.pop(run.id, None)

    def _mark_running(self, run_id: str) -> WorkflowRun:
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            runs = WorkflowRepository(connection)
            run = runs.get(run_id)
            if run is None:
                raise WorkflowNotFoundError(f"workflow not found: {run_id}")
            if run.status.is_terminal:
                raise ConflictError("workflow became terminal before execution")
            now = utc_now()
            running = replace(
                run,
                status=WorkflowStatus.RUNNING,
                started_at=run.started_at or now,
                updated_at=now,
            )
            runs.update(running)
            turns = TurnRepository(connection)
            turn = turns.get(run.turn_id)
            if turn is None:
                raise ConflictError("workflow turn is missing")
            running_turn = replace(
                turn,
                status=TurnStatus.RUNNING,
                started_at=turn.started_at or now,
            )
            turns.update(running_turn)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="workflow.updated",
                        payload={"workflow": workflow_view(running)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="turn.updated",
                        payload={"turn": turn_view(running_turn)},
                    ),
                )
            )
        self._publish(events)
        return running

    async def _progress(
        self,
        run_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        message: str,
        metadata: JsonObject,
    ) -> None:
        clean_stage = stage.strip()[:80] or "working"
        clean_message = message.strip()[:500] or clean_stage
        clean_metadata = _bounded_json(metadata, "metadata")
        safe_total = max(1, min(int(total), 1_000_000))
        safe_current = max(0, min(int(current), safe_total))
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            runs = WorkflowRepository(connection)
            run = runs.get(run_id)
            if (
                run is None
                or run.status.is_terminal
                or run.status is WorkflowStatus.WAITING
            ):
                return
            now = utc_now()
            items = ItemRepository(connection)
            checkpoint = dict(run.checkpoint)
            previous_item = items.get(str(checkpoint.get("stageItemId", "")))
            event_repo = EventRepository(connection)
            if (
                previous_item is not None
                and previous_item.status is ItemStatus.IN_PROGRESS
                and run.current_stage == clean_stage
            ):
                stage_item = replace(
                    previous_item,
                    summary=clean_message,
                    payload={
                        "stage": clean_stage,
                        "current": safe_current,
                        "total": safe_total,
                        "metadata": clean_metadata,
                    },
                    updated_at=now,
                )
                items.update(stage_item)
                item_event_type = "item.updated"
            else:
                if previous_item is not None and previous_item.status in {
                    ItemStatus.PENDING,
                    ItemStatus.IN_PROGRESS,
                }:
                    settled = replace(
                        previous_item, status=ItemStatus.COMPLETED, updated_at=now
                    )
                    items.update(settled)
                    events.append(
                        event_repo.append(
                            thread_id=run.thread_id,
                            turn_id=run.turn_id,
                            item_id=settled.id,
                            type="item.updated",
                            payload={"item": item_view(settled)},
                        )
                    )
                stage_item = Item(
                    thread_id=run.thread_id,
                    turn_id=run.turn_id,
                    ordinal=items.next_ordinal(run.turn_id),
                    kind=ItemKind.WORKFLOW_STAGE,
                    status=ItemStatus.IN_PROGRESS,
                    summary=clean_message,
                    payload={
                        "stage": clean_stage,
                        "current": safe_current,
                        "total": safe_total,
                        "metadata": clean_metadata,
                    },
                    created_at=now,
                    updated_at=now,
                )
                items.add(stage_item)
                item_event_type = "item.created"
            checkpoint["stageItemId"] = stage_item.id
            checkpoint.update(
                {
                    key: value
                    for key, value in clean_metadata.items()
                    if key in {"taskId", "workflowRoot"}
                }
            )
            updated = replace(
                run,
                status=WorkflowStatus.RUNNING,
                current_stage=clean_stage,
                progress_current=safe_current,
                progress_total=safe_total,
                checkpoint=checkpoint,
                updated_at=now,
            )
            runs.update(updated)
            events.extend(
                (
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=stage_item.id,
                        type=item_event_type,
                        payload={"item": item_view(stage_item)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="workflow.updated",
                        payload={"workflow": workflow_view(updated)},
                    ),
                )
            )
        self._publish(events)
        task_id = str(updated.checkpoint.get("taskId") or updated.id)
        self.session_store.update_task_status(
            updated.thread_id,
            task_id,
            "running",
            metadata={
                "workflowRunId": updated.id,
                "stage": clean_stage,
                "progressCurrent": safe_current,
                "progressTotal": safe_total,
                **{
                    key: value
                    for key, value in clean_metadata.items()
                    if key in {"taskId", "workflowRoot"}
                },
            },
        )

    async def _interact(self, run_id: str, request: JsonObject) -> JsonObject:
        clean_request = _bounded_json(request, "interaction")
        interaction_id = new_id("wfi")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[JsonObject] = loop.create_future()
        events: list[DomainEvent] = []
        with self._interaction_lock:
            if run_id in self._interactions:
                raise WorkflowInteractionError("workflow already has an interaction")
        with self.database.transaction() as connection:
            runs = WorkflowRepository(connection)
            run = runs.get(run_id)
            if run is None or run.status.is_terminal:
                raise WorkflowInteractionError("workflow is no longer active")
            now = utc_now()
            items = ItemRepository(connection)
            item = Item(
                thread_id=run.thread_id,
                turn_id=run.turn_id,
                ordinal=items.next_ordinal(run.turn_id),
                kind=ItemKind.PLAN,
                status=ItemStatus.PENDING,
                summary=str(clean_request.get("message") or "Review workflow plan")[
                    :500
                ],
                payload={
                    "workflowRunId": run.id,
                    "interactionId": interaction_id,
                    "request": clean_request,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(item)
            checkpoint = dict(run.checkpoint)
            checkpoint["interaction"] = {
                "id": interaction_id,
                "itemId": item.id,
                "request": clean_request,
            }
            waiting = replace(
                run,
                status=WorkflowStatus.WAITING,
                checkpoint=checkpoint,
                updated_at=now,
            )
            runs.update(waiting)
            turns = TurnRepository(connection)
            turn = turns.get(run.turn_id)
            if turn is None:
                raise ConflictError("workflow turn is missing")
            waiting_turn = replace(turn, status=TurnStatus.WAITING_APPROVAL)
            turns.update(waiting_turn)
            threads = ThreadRepository(connection)
            thread = threads.get(run.thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {run.thread_id}")
            waiting_thread = replace(
                thread, status=ThreadStatus.WAITING, updated_at=now
            )
            threads.update(waiting_thread)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=item.id,
                        type="item.created",
                        payload={"item": item_view(item)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="workflow.interaction_requested",
                        payload={"workflow": workflow_view(waiting)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="turn.updated",
                        payload={"turn": turn_view(waiting_turn)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(waiting_thread)},
                    ),
                )
            )
        with self._interaction_lock:
            self._interactions[run_id] = _InteractionWaiter(loop, future)
        self._publish(events)
        try:
            return await future
        finally:
            with self._interaction_lock:
                current = self._interactions.get(run_id)
                if current is not None and current.future is future:
                    self._interactions.pop(run_id, None)

    def _finish(
        self,
        run_id: str,
        *,
        status: WorkflowStatus,
        outcome: WorkflowOutcome | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        checkpoint_patch: JsonObject | None = None,
        turn_status: TurnStatus | None = None,
        turn_stop_reason: str | None = None,
    ) -> WorkflowRun:
        if not status.is_terminal:
            raise ValueError("finish requires a terminal workflow status")
        current = self.read(run_id).run
        if current.status.is_terminal:
            return current
        artifacts = self._prepare_artifacts(current, outcome)
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            runs = WorkflowRepository(connection)
            run = runs.get(run_id)
            if run is None:
                raise WorkflowNotFoundError(f"workflow not found: {run_id}")
            if run.status.is_terminal:
                return run
            now = utc_now()
            checkpoint = dict(run.checkpoint)
            checkpoint.pop("interaction", None)
            if checkpoint_patch:
                checkpoint.update(checkpoint_patch)
            result = outcome.result if outcome is not None else run.result
            terminal = replace(
                run,
                status=status,
                result=result,
                checkpoint=checkpoint,
                progress_current=(run.progress_total or 100)
                if status is WorkflowStatus.COMPLETED
                else run.progress_current,
                progress_total=(run.progress_total or 100)
                if status is WorkflowStatus.COMPLETED
                else run.progress_total,
                updated_at=now,
                completed_at=now,
                error_code=error_code if status is WorkflowStatus.FAILED else None,
                error_message=(error_message or "workflow failed")[:MAX_SUMMARY_LENGTH]
                if status is WorkflowStatus.FAILED
                else None,
            )
            runs.update(terminal)
            items = ItemRepository(connection)
            event_repo = EventRepository(connection)
            for active_item in items.list_active_for_turn(run.turn_id):
                settled = replace(
                    active_item,
                    status=ItemStatus.COMPLETED
                    if status is WorkflowStatus.COMPLETED
                    else ItemStatus.FAILED,
                    updated_at=now,
                )
                items.update(settled)
                events.append(
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=settled.id,
                        type="item.updated",
                        payload={"item": item_view(settled)},
                    )
                )
            artifact_repo = ArtifactRepository(connection)
            for artifact in artifacts:
                artifact_repo.add(artifact)
                artifact_item = Item(
                    thread_id=run.thread_id,
                    turn_id=run.turn_id,
                    ordinal=items.next_ordinal(run.turn_id),
                    kind=ItemKind.ARTIFACT,
                    status=ItemStatus.COMPLETED,
                    summary=artifact.name[:500],
                    payload={"artifact": artifact_view(artifact)},
                    created_at=now,
                    updated_at=now,
                )
                items.add(artifact_item)
                events.append(
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=artifact_item.id,
                        type="artifact.created",
                        payload={
                            "artifact": artifact_view(artifact),
                            "item": item_view(artifact_item),
                        },
                    )
                )
            summary = (
                outcome.summary
                if outcome is not None
                else error_message
                or (
                    "Workflow cancelled"
                    if status is WorkflowStatus.CANCELLED
                    else "Workflow stopped"
                )
            )[:MAX_SUMMARY_LENGTH]
            completion = Item(
                thread_id=run.thread_id,
                turn_id=run.turn_id,
                ordinal=items.next_ordinal(run.turn_id),
                kind=ItemKind.COMPLETION,
                status=ItemStatus.COMPLETED
                if status is WorkflowStatus.COMPLETED
                else ItemStatus.FAILED,
                summary=summary,
                payload={
                    "workflowRunId": run.id,
                    "status": status.value,
                    "errorCode": terminal.error_code,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(completion)
            turns = TurnRepository(connection)
            turn = turns.get(run.turn_id)
            if turn is None:
                raise ConflictError("workflow turn is missing")
            resolved_turn_status = turn_status or (
                TurnStatus.COMPLETED
                if status is WorkflowStatus.COMPLETED
                else TurnStatus.INTERRUPTED
                if status is WorkflowStatus.CANCELLED
                else TurnStatus.FAILED
            )
            terminal_turn = replace(
                turn,
                status=resolved_turn_status,
                stop_reason=turn_stop_reason or status.value,
                error_code=terminal.error_code
                if resolved_turn_status is TurnStatus.FAILED
                else None,
                error_message=terminal.error_message
                if resolved_turn_status is TurnStatus.FAILED
                else None,
                completed_at=now,
            )
            turns.update(terminal_turn)
            threads = ThreadRepository(connection)
            thread = threads.get(run.thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {run.thread_id}")
            settled_thread = replace(
                thread,
                status=ThreadStatus.FAILED
                if status is WorkflowStatus.FAILED
                else ThreadStatus.IDLE,
                updated_at=now,
            )
            threads.update(settled_thread)
            events.extend(
                (
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        item_id=completion.id,
                        type="item.created",
                        payload={"item": item_view(completion)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="workflow.completed",
                        payload={"workflow": workflow_view(terminal)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=run.turn_id,
                        type="turn.completed",
                        payload={"turn": turn_view(terminal_turn)},
                    ),
                    event_repo.append(
                        thread_id=run.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(settled_thread)},
                    ),
                )
            )
        self._publish(events)
        self._persist_workflow_terminal(terminal, summary)
        return terminal

    def _persist_workflow_start(
        self,
        run: WorkflowRun,
        prompt: str,
        workspace: Path,
    ) -> None:
        session = self.session_store.get_session(run.thread_id)
        if session is None:
            raise ThreadNotFoundError(f"session not found: {run.thread_id}")
        task_id = str(run.checkpoint.get("taskId") or run.id)
        attached = self.session_store.attach_task(
            run.thread_id,
            task_id,
            task_kind="paper",
            task_dir=workspace,
            status="running",
            metadata={
                "workflowRunId": run.id,
                "turnId": run.turn_id,
                "attempt": run.attempt,
                "retryOf": run.retry_of,
                "input": run.input,
            },
        )
        if attached is None:
            raise RuntimeError("could not attach workflow to canonical session")
        if not any(
            message.metadata
            and message.metadata.get("workflowRunId") == run.id
            and message.metadata.get("workflowStart") is True
            for message in session.messages
        ):
            stored = self.session_store.append_message(
                run.thread_id,
                "user",
                prompt,
                task_id_ref=task_id,
                metadata={
                    "client": "desktop",
                    "workflowRunId": run.id,
                    "workflowStart": True,
                },
            )
            if stored is None:
                raise RuntimeError("could not persist workflow prompt")

    def _persist_workflow_terminal(
        self,
        run: WorkflowRun,
        summary: str,
    ) -> None:
        task_id = str(run.checkpoint.get("taskId") or run.id)
        updated_task = self.session_store.update_task_status(
            run.thread_id,
            task_id,
            run.status.value,
            metadata={
                "workflowRunId": run.id,
                "attempt": run.attempt,
                "result": run.result,
                "checkpoint": run.checkpoint,
                "errorCode": run.error_code,
                "errorMessage": run.error_message,
            },
        )
        if updated_task is None:
            logging.getLogger(__name__).error(
                "workflow %s completed without canonical task %s",
                run.id,
                task_id,
            )
        session = self.session_store.get_session(run.thread_id)
        if session is None:
            logging.getLogger(__name__).error(
                "workflow %s completed without canonical session %s",
                run.id,
                run.thread_id,
            )
            return
        if any(
            message.metadata
            and message.metadata.get("workflowRunId") == run.id
            and message.metadata.get("workflowTerminal") is True
            for message in session.messages
        ):
            return
        stored = self.session_store.append_message(
            run.thread_id,
            "assistant",
            summary,
            task_id_ref=task_id,
            metadata={
                "client": "desktop",
                "workflowRunId": run.id,
                "workflowTerminal": True,
                "status": run.status.value,
            },
        )
        if stored is None:
            logging.getLogger(__name__).error(
                "workflow %s could not persist its terminal message",
                run.id,
            )

    def _prepare_artifacts(
        self, run: WorkflowRun, outcome: WorkflowOutcome | None
    ) -> tuple[Artifact, ...]:
        if outcome is None:
            return ()
        context = self.workspaces.resolve(run.thread_id)
        artifacts: list[Artifact] = []
        for spec in outcome.artifacts:
            try:
                resolved = spec.path.expanduser().resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_relative_to(context.root):
                continue
            if not (resolved.is_file() or resolved.is_dir()):
                continue
            relative = resolved.relative_to(context.root).as_posix()
            artifacts.append(
                Artifact(
                    thread_id=run.thread_id,
                    turn_id=run.turn_id,
                    workflow_run_id=run.id,
                    kind=spec.kind[:100] or "artifact",
                    name=spec.name[:500] or resolved.name,
                    media_type=spec.media_type[:200] or "application/octet-stream",
                    storage_path=relative,
                    byte_size=resolved.stat().st_size if resolved.is_file() else None,
                    metadata=_bounded_json(spec.metadata, "artifact metadata"),
                )
            )
        return tuple(artifacts)

    def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.broker.publish(event)


def _validate_input(
    kind: str, source_type: str, source: str, options: JsonObject
) -> tuple[str, str, str, JsonObject]:
    clean_kind = kind.strip().lower()
    if clean_kind not in SUPPORTED_KINDS:
        raise InvalidArgumentError(f"unsupported workflow kind: {kind}")
    clean_source_type = source_type.strip().lower()
    if clean_source_type not in SUPPORTED_SOURCE_TYPES:
        raise InvalidArgumentError(f"unsupported workflow source type: {source_type}")
    clean_source = source.strip()
    if not clean_source or len(clean_source) > MAX_SOURCE_LENGTH:
        raise InvalidArgumentError(
            f"workflow source must be between 1 and {MAX_SOURCE_LENGTH} characters"
        )
    if clean_source_type == "url":
        parsed = urlsplit(clean_source)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidArgumentError("workflow URL must use HTTP or HTTPS")
        if parsed.username or parsed.password:
            raise InvalidArgumentError("workflow URL must not contain credentials")
    clean_options = _bounded_json(options, "options")
    allowed = {"enableIndexing", "planReview"}
    unknown = set(clean_options) - allowed
    if unknown:
        raise InvalidArgumentError(
            f"unsupported workflow option(s): {', '.join(sorted(unknown))}"
        )
    for key, value in clean_options.items():
        if not isinstance(value, bool):
            raise InvalidArgumentError(f"workflow option {key} must be boolean")
    return clean_kind, clean_source_type, clean_source, clean_options


def _require_object(value: Any, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise InvalidArgumentError(f"{name} must be an object")
    return value


def _bounded_json(value: JsonObject, name: str) -> JsonObject:
    validate_json_object(value, name)
    import json

    if len(json.dumps(value, ensure_ascii=False)) > 64 * 1024:
        raise InvalidArgumentError(f"{name} exceeds 64 KiB")
    return dict(value)


def _workflow_prompt(source_type: str, source: str) -> str:
    display = Path(source).name if source_type == "local" else source
    return f"Run Paper2Code from {source_type}: {display}"[:2_000]


def _is_text_artifact(artifact: Artifact, path: Path) -> bool:
    if artifact.media_type.startswith("text/"):
        return True
    return path.suffix.lower() in {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".html",
    }
