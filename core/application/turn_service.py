"""Durable turn orchestration over the shared AgentSession kernel."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from core.application.agent_adapter import (
    AgentSessionFactory,
    DefaultAgentSessionFactory,
)
from core.application.approval_service import ApprovalService
from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
    TurnAlreadyRunningError,
    TurnNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.application.event_service import EventBroker
from core.application.execution_registry import ExecutionRegistry
from core.application.session_runtime import SessionRuntimeRegistry
from core.application.turn_projection import TurnEventProjector
from core.application.views import (
    approval_view,
    item_view,
    thread_view,
    turn_view,
)
from core.domain.approval import Approval, ApprovalStatus
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.project import TrustState
from core.domain.thread import Thread, ThreadStatus
from core.domain.turn import Turn, TurnStatus
from core.events import Event, SkillLoaded, TurnStarted, UserInput
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import (
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
)
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore
from core.skills.models import SkillInvocation, SkillSelection


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    turn: Turn
    items: tuple[Item, ...]
    approvals: tuple[Approval, ...]


class TurnService:
    """One active turn per thread, executed on a bounded shared runtime."""

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        approvals: ApprovalService,
        registry: ExecutionRegistry,
        *,
        session_factory: AgentSessionFactory | None = None,
        session_store: SessionStore,
    ) -> None:
        self.database = database
        self.broker = broker
        self.approvals = approvals
        self.registry = registry
        self.session_factory = session_factory or DefaultAgentSessionFactory()
        self.session_store = session_store
        self.session_runtimes = SessionRuntimeRegistry(
            session_store,
            self.session_factory,
        )
        self._observer_lock = threading.Lock()
        self._event_observers: dict[str, Callable[[Event], None]] = {}

    def start(
        self,
        thread_id: str,
        *,
        prompt: str,
        skill_ids: tuple[str, ...] = (),
        event_observer: Callable[[Event], None] | None = None,
    ) -> TurnSnapshot:
        return self._submit(
            thread_id,
            prompt=prompt,
            skill_ids=skill_ids,
            queue_if_busy=False,
            event_observer=event_observer,
        )

    def enqueue(
        self,
        thread_id: str,
        *,
        prompt: str,
        skill_ids: tuple[str, ...] = (),
        event_observer: Callable[[Event], None] | None = None,
    ) -> TurnSnapshot:
        """Persist a next Turn and run it after earlier Turns settle."""

        return self._submit(
            thread_id,
            prompt=prompt,
            skill_ids=skill_ids,
            queue_if_busy=True,
            event_observer=event_observer,
        )

    def _submit(
        self,
        thread_id: str,
        *,
        prompt: str,
        skill_ids: tuple[str, ...],
        queue_if_busy: bool,
        event_observer: Callable[[Event], None] | None,
    ) -> TurnSnapshot:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise InvalidArgumentError("turn prompt must not be empty")
        try:
            clean_skill_ids = tuple(
                SkillSelection(skill_id=skill_id).skill_id for skill_id in skill_ids
            )
            if len(set(clean_skill_ids)) != len(clean_skill_ids):
                raise ValueError("skill IDs must be unique")
            if len(clean_skill_ids) > 8:
                raise ValueError("a turn may select at most 8 Skills")
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        events: list[DomainEvent] = []
        schedule_now = False
        with self.database.transaction() as connection:
            threads = ThreadRepository(connection)
            thread = threads.get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            if thread.status is ThreadStatus.ARCHIVED:
                raise ConflictError("cannot start a turn in an archived thread")
            project = ProjectRepository(connection).get(thread.project_id)
            if project is None:
                raise ConflictError("thread project is missing")
            if project.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must be trusted before agent execution"
                )
            self._validate_workspace(thread, project.canonical_path)
            turns = TurnRepository(connection)
            active = turns.active_for_thread(thread_id)
            if active is not None and not queue_if_busy:
                raise TurnAlreadyRunningError(
                    f"thread already has an active turn: {active.id}"
                )
            schedule_now = active is None
            turn = Turn(
                thread_id=thread_id,
                ordinal=turns.next_ordinal(thread_id),
                prompt=clean_prompt,
                skill_ids=clean_skill_ids,
            )
            turns.add(turn)
            items = ItemRepository(connection)
            now = utc_now()
            user_item = Item(
                thread_id=thread_id,
                turn_id=turn.id,
                ordinal=items.next_ordinal(turn.id),
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=clean_prompt[:160],
                payload={
                    "text": clean_prompt,
                    "skillIds": list(clean_skill_ids),
                    "skills": [],
                },
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
                        type="turn.started" if schedule_now else "turn.queued",
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
                        type="thread.status_changed",
                        payload={"thread": thread_view(running_thread)},
                    ),
                )
            )
        self._publish(events)
        if event_observer is not None:
            with self._observer_lock:
                self._event_observers[turn.id] = event_observer
        if schedule_now:
            self._schedule(turn.id)
        return TurnSnapshot(turn, (user_item,), ())

    def _schedule(self, turn_id: str, *, propagate: bool = True) -> None:
        try:
            self.registry.start(
                turn_id,
                lambda: self._execute(turn_id),
                on_cancelled_before_start=lambda: self._cancel_before_start(turn_id),
            )
        except Exception as exc:
            try:
                self._finish_unstarted(
                    turn_id,
                    status=TurnStatus.FAILED,
                    stop_reason="scheduler_error",
                    error_code="SCHEDULER_ERROR",
                    error_message=str(exc),
                )
            finally:
                self._remove_observer(turn_id)
            if propagate:
                raise

    def read(self, turn_id: str) -> TurnSnapshot:
        with self.database.read() as connection:
            turn = TurnRepository(connection).get(turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            items = ItemRepository(connection).list_for_turn(turn_id)
            approvals = ApprovalRepository(connection).list_for_turn(turn_id)
        return TurnSnapshot(turn, tuple(items), tuple(approvals))

    def conversation_count(self, thread_id: str) -> int:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            return ItemRepository(connection).conversation_count(thread_id)

    def interrupt(self, turn_id: str) -> tuple[bool, Turn]:
        snapshot = self.read(turn_id)
        if snapshot.turn.status.is_terminal:
            return False, snapshot.turn
        accepted = self.registry.interrupt(turn_id)
        if not accepted:
            try:
                self._finish_unstarted(
                    turn_id,
                    status=TurnStatus.INTERRUPTED,
                    stop_reason="interrupted",
                )
            finally:
                self._remove_observer(turn_id)
        return True, self.read(turn_id).turn

    async def _execute(self, turn_id: str) -> None:
        projection: TurnEventProjector | None = None
        session_thread_id: str | None = None
        session_acquired = False
        status = TurnStatus.FAILED
        stop_reason = "protocol_incomplete"
        error_code: str | None = "PROTOCOL_INCOMPLETE"
        error_message: str | None = "agent stream ended without task_complete"
        try:
            turn, workspace, model = self._mark_running(turn_id)
            session_thread_id = turn.thread_id
            projection = TurnEventProjector(
                self.database,
                self.broker,
                thread_id=turn.thread_id,
                turn_id=turn.id,
            )

            async def approve(
                tool_name: str,
                arguments: dict,
                reason: str | None,
            ) -> bool:
                return await self.approvals.request(
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=reason,
                )

            session = await self.session_runtimes.acquire(
                turn.thread_id,
                workspace=workspace,
                model=model,
                approval_callback=approve,
            )
            session_acquired = True
            skill_invocations: dict[str, SkillInvocation] = {}
            stored_user = False
            async for event in session.run_stream(
                UserInput(
                    text=turn.prompt,
                    skills=tuple(
                        SkillSelection(skill_id=skill_id) for skill_id in turn.skill_ids
                    ),
                )
            ):
                if isinstance(event.msg, TurnStarted):
                    for invocation in event.msg.skill_invocations:
                        skill_invocations[invocation.skill_id] = invocation
                    if not stored_user:
                        stored = self.session_store.append_message(
                            turn.thread_id,
                            "user",
                            turn.prompt,
                            metadata={
                                "schemaVersion": 2,
                                "client": "desktop",
                                "turnId": turn.id,
                                "skillInvocations": [
                                    invocation.to_metadata()
                                    for invocation in skill_invocations.values()
                                ],
                            },
                        )
                        if stored is None:
                            raise RuntimeError(
                                f"canonical session disappeared: {turn.thread_id}"
                            )
                        stored_user = True
                        self.session_runtimes.mark_persisted(turn.thread_id)
                elif isinstance(event.msg, SkillLoaded):
                    invocation = event.msg.invocation
                    skill_invocations[invocation.skill_id] = invocation
                self._notify_observer(turn_id, event)
                projection.project(event)
            if projection.saw_terminal:
                if projection.final_text:
                    stored_assistant = self.session_store.append_message(
                        turn.thread_id,
                        "assistant",
                        projection.final_text,
                        metadata={
                            "schemaVersion": 2,
                            "client": "desktop",
                            "turnId": turn.id,
                            "skillInvocations": [
                                invocation.to_metadata()
                                for invocation in skill_invocations.values()
                            ],
                        },
                    )
                    if stored_assistant is None:
                        raise RuntimeError(
                            f"canonical session disappeared: {turn.thread_id}"
                        )
                if stored_user or projection.final_text:
                    self.session_runtimes.mark_persisted(turn.thread_id)
                stop_reason = projection.stop_reason or "completed"
                if stop_reason == "interrupted":
                    status = TurnStatus.INTERRUPTED
                    error_code = error_message = None
                elif stop_reason in {
                    "error",
                    "empty_final_response",
                    "busy",
                    "invalid_skill",
                }:
                    status = TurnStatus.FAILED
                    error_code = "AGENT_TURN_FAILED"
                    error_message = f"agent stopped with reason: {stop_reason}"
                else:
                    status = TurnStatus.COMPLETED
                    error_code = error_message = None
        except asyncio.CancelledError:
            status = TurnStatus.INTERRUPTED
            stop_reason = "interrupted"
            error_code = error_message = None
        except Exception as exc:  # noqa: BLE001 - persisted as stable turn failure
            status = TurnStatus.FAILED
            stop_reason = "error"
            error_code = "AGENT_EXECUTION_ERROR"
            error_message = f"{type(exc).__name__}: {exc}"
            if projection is not None:
                projection.add_error(code=error_code, message=error_message)
        finally:
            if session_acquired and session_thread_id is not None:
                self.session_runtimes.release(session_thread_id)

        if projection is not None:
            try:
                projection.close_open_items(
                    interrupted=status is TurnStatus.INTERRUPTED
                )
                projection.add_completion(
                    status=ItemStatus.COMPLETED
                    if status is TurnStatus.COMPLETED
                    else ItemStatus.FAILED,
                    stop_reason=stop_reason,
                )
            except Exception as exc:  # noqa: BLE001 - still persist a terminal turn
                status = TurnStatus.FAILED
                stop_reason = "projection_error"
                error_code = "EVENT_PROJECTION_ERROR"
                error_message = f"{type(exc).__name__}: {exc}"
        try:
            self._finish(
                turn_id,
                status=status,
                stop_reason=stop_reason,
                error_code=error_code,
                error_message=error_message,
            )
        finally:
            self._remove_observer(turn_id)

    def _cancel_before_start(self, turn_id: str) -> None:
        try:
            self._finish_unstarted(
                turn_id,
                status=TurnStatus.INTERRUPTED,
                stop_reason="interrupted",
            )
        finally:
            self._remove_observer(turn_id)

    def _finish_unstarted(
        self,
        turn_id: str,
        *,
        status: TurnStatus,
        stop_reason: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Turn:
        snapshot = self.read(turn_id)
        if not any(item.kind is ItemKind.COMPLETION for item in snapshot.items):
            TurnEventProjector(
                self.database,
                self.broker,
                thread_id=snapshot.turn.thread_id,
                turn_id=turn_id,
            ).add_completion(status=ItemStatus.FAILED, stop_reason=stop_reason)
        return self._finish(
            turn_id,
            status=status,
            stop_reason=stop_reason,
            error_code=error_code,
            error_message=error_message,
        )

    def _notify_observer(self, turn_id: str, event: Event) -> None:
        with self._observer_lock:
            observer = self._event_observers.get(turn_id)
        if observer is None:
            return
        try:
            observer(event)
        except Exception:  # noqa: BLE001 - a client observer cannot fail the turn
            logging.getLogger(__name__).exception(
                "turn event observer failed for %s",
                turn_id,
            )

    def _remove_observer(self, turn_id: str) -> None:
        with self._observer_lock:
            self._event_observers.pop(turn_id, None)

    async def close_live_sessions(self) -> None:
        """Release AgentControl, tools, and hooks for every loaded Thread."""

        await self.session_runtimes.close_all()

    def _mark_running(self, turn_id: str) -> tuple[Turn, str, str | None]:
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            turn = turns.get(turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            if turn.status.is_terminal:
                raise ConflictError("turn became terminal before execution")
            now = utc_now()
            running = replace(
                turn,
                status=TurnStatus.RUNNING,
                started_at=turn.started_at or now,
            )
            turns.update(running)
            thread = ThreadRepository(connection).get(turn.thread_id)
            if thread is None:
                raise ConflictError("turn thread is missing")
            event = EventRepository(connection).append(
                thread_id=turn.thread_id,
                turn_id=turn.id,
                type="turn.updated",
                payload={"turn": turn_view(running)},
            )
        self.broker.publish(event)
        return running, thread.workspace_path, thread.model

    def _finish(
        self,
        turn_id: str,
        *,
        status: TurnStatus,
        stop_reason: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Turn:
        if not status.is_terminal:
            raise ValueError("finish requires a terminal status")
        events: list[DomainEvent] = []
        schedule_next_id: str | None = None
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            current = turns.get(turn_id)
            if current is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            if current.status.is_terminal:
                return current
            now = utc_now()
            terminal = replace(
                current,
                status=status,
                stop_reason=stop_reason,
                error_code=error_code if status is TurnStatus.FAILED else None,
                error_message=error_message if status is TurnStatus.FAILED else None,
                completed_at=now,
            )
            turns.update(terminal)
            threads = ThreadRepository(connection)
            thread = threads.get(current.thread_id)
            if thread is None:
                raise ConflictError("turn thread is missing")
            executing = turns.executing_for_thread(current.thread_id)
            next_queued = turns.next_queued_for_thread(current.thread_id)
            if executing is not None:
                thread_status = (
                    ThreadStatus.WAITING
                    if executing.status is TurnStatus.WAITING_APPROVAL
                    else ThreadStatus.RUNNING
                )
            elif next_queued is not None:
                thread_status = ThreadStatus.RUNNING
                schedule_next_id = next_queued.id
            else:
                thread_status = (
                    ThreadStatus.FAILED
                    if status is TurnStatus.FAILED
                    else ThreadStatus.IDLE
                )
            settled_thread = replace(
                thread,
                status=thread_status,
                updated_at=now,
            )
            threads.update(settled_thread)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=turn_id,
                        type="turn.completed",
                        payload={"turn": turn_view(terminal)},
                    ),
                    event_repo.append(
                        thread_id=current.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(settled_thread)},
                    ),
                )
            )
        self._publish(events)
        if schedule_next_id is not None:
            self._schedule(schedule_next_id, propagate=False)
        return terminal

    def recover_incomplete(self) -> int:
        """Interrupt lost live work and resume durable queued Turns."""

        events: list[DomainEvent] = []
        schedule_ids: list[str] = []
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            items = ItemRepository(connection)
            approvals = ApprovalRepository(connection)
            threads = ThreadRepository(connection)
            event_repo = EventRepository(connection)
            active_turns = turns.list_active()
            affected_threads = {turn.thread_id for turn in active_turns}
            for turn in active_turns:
                if turn.status is TurnStatus.QUEUED:
                    continue
                now = utc_now()
                for approval in approvals.pending_for_turn(turn.id):
                    cancelled = replace(
                        approval,
                        status=ApprovalStatus.CANCELLED,
                        decision={
                            "status": ApprovalStatus.CANCELLED.value,
                            "reason": "application_restarted",
                        },
                        resolved_at=now,
                    )
                    approvals.update(cancelled)
                    events.append(
                        event_repo.append(
                            thread_id=turn.thread_id,
                            turn_id=turn.id,
                            item_id=approval.item_id,
                            type="approval.resolved",
                            payload={"approval": approval_view(cancelled)},
                        )
                    )
                for item in items.list_active_for_turn(turn.id):
                    settled = replace(
                        item,
                        status=ItemStatus.DECLINED
                        if item.kind is ItemKind.APPROVAL_REQUEST
                        else ItemStatus.FAILED,
                        payload={**item.payload, "recoveredAfterRestart": True},
                        updated_at=now,
                    )
                    items.update(settled)
                    events.append(
                        event_repo.append(
                            thread_id=turn.thread_id,
                            turn_id=turn.id,
                            item_id=settled.id,
                            type="item.updated",
                            payload={"item": item_view(settled)},
                        )
                    )
                completion = Item(
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    ordinal=items.next_ordinal(turn.id),
                    kind=ItemKind.COMPLETION,
                    status=ItemStatus.FAILED,
                    summary="Turn interrupted after application restart",
                    payload={"stopReason": "application_restarted"},
                    created_at=now,
                    updated_at=now,
                )
                items.add(completion)
                events.append(
                    event_repo.append(
                        thread_id=turn.thread_id,
                        turn_id=turn.id,
                        item_id=completion.id,
                        type="item.created",
                        payload={"item": item_view(completion)},
                    )
                )
                interrupted = replace(
                    turn,
                    status=TurnStatus.INTERRUPTED,
                    stop_reason="application_restarted",
                    completed_at=now,
                )
                turns.update(interrupted)
                events.append(
                    event_repo.append(
                        thread_id=turn.thread_id,
                        turn_id=turn.id,
                        type="turn.recovered",
                        payload={"turn": turn_view(interrupted)},
                    )
                )
            for thread_id in sorted(affected_threads):
                thread = threads.get(thread_id)
                if thread is None or thread.status is ThreadStatus.ARCHIVED:
                    continue
                next_queued = turns.next_queued_for_thread(thread_id)
                now = utc_now()
                settled_thread = replace(
                    thread,
                    status=(
                        ThreadStatus.RUNNING
                        if next_queued is not None
                        else ThreadStatus.IDLE
                    ),
                    updated_at=now,
                )
                threads.update(settled_thread)
                events.append(
                    event_repo.append(
                        thread_id=thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(settled_thread)},
                    )
                )
                if next_queued is not None:
                    schedule_ids.append(next_queued.id)
        self._publish(events)
        for turn_id in schedule_ids:
            self._schedule(turn_id, propagate=False)
        return len(active_turns)

    @staticmethod
    def _validate_workspace(thread: Thread, project_path: str) -> None:
        try:
            workspace = Path(thread.workspace_path).resolve(strict=True)
            project = Path(project_path).resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError("workspace no longer exists") from exc
        if not workspace.is_dir():
            raise InvalidArgumentError("workspace path must be a directory")
        if thread.worktree_path is None:
            if not workspace.is_relative_to(project):
                raise WorkspaceOutOfScopeError(
                    f"workspace is outside project boundary: {workspace}"
                )
            return
        try:
            worktree = Path(thread.worktree_path).resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError("worktree no longer exists") from exc
        if workspace != worktree or not (worktree / ".git").exists():
            raise WorkspaceOutOfScopeError("thread worktree ownership is invalid")

    def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.broker.publish(event)
