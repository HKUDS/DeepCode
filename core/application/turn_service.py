"""Durable turn orchestration over the shared AgentSession kernel."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from core.agent_runtime.goal_runtime import (
    GoalRuntimeContext,
    GoalRuntimeHandler,
)
from core.application.agent_adapter import (
    AgentSessionFactory,
    DefaultAgentSessionFactory,
)
from core.application.approval_service import ApprovalService
from core.application.errors import (
    ConflictError,
    DuplicateMessageConflictError,
    EmptyInputError,
    ExpectedTurnMismatchError,
    InvalidArgumentError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
    TurnAlreadyRunningError,
    TurnInterruptTimeoutError,
    TurnNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.application.event_service import EventBroker
from core.application.execution_registry import ExecutionRegistry
from core.application.llm_configuration_service import LLMConfigurationService
from core.application.goal_turn_port import (
    GoalContextProvider,
    GoalTurnAssociation,
)
from core.application.session_runtime import SessionRuntimeRegistry
from core.application.turn_input_service import (
    TurnInputReceipt,
    TurnInputService,
)
from core.application.turn_projection import TurnEventProjector
from core.application.turn_usage import (
    TURN_USAGE_EVENT_TYPE,
    aggregate_recorded_usage,
    normalize_usage,
)
from core.application.views import (
    approval_view,
    item_view,
    thread_view,
    turn_view,
)
from core.domain.approval import Approval, ApprovalStatus
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import (
    ClientSurface,
    TurnInputDelivery,
    TurnInputSource,
)
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
from core.sessions.continuation import assistant_continuation_metadata
from core.skills.models import MAX_SELECTED_SKILLS, SkillInvocation, SkillSelection

TurnSettledListener = Callable[[Turn], None]


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    turn: Turn
    items: tuple[Item, ...]
    approvals: tuple[Approval, ...]


@dataclass(frozen=True, slots=True)
class _TurnSubmission:
    snapshot: TurnSnapshot
    duplicate: bool = False


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
        llm_configuration: LLMConfigurationService | None = None,
    ) -> None:
        self.database = database
        self.broker = broker
        self.approvals = approvals
        self.registry = registry
        self.session_factory = session_factory or DefaultAgentSessionFactory()
        self.session_store = session_store
        self.llm_configuration = llm_configuration or LLMConfigurationService()
        self.session_runtimes = SessionRuntimeRegistry(
            session_store,
            self.session_factory,
        )
        self.turn_inputs = TurnInputService(
            database,
            self.session_runtimes,
            session_store,
            self._publish,
        )
        self._observer_lock = threading.Lock()
        self._event_observers: dict[str, Callable[[Event], None]] = {}
        self._thread_event_observers: dict[
            str,
            tuple[str, Callable[[Event], None]],
        ] = {}
        self._settled_listener_lock = threading.Lock()
        self._settled_listeners: list[TurnSettledListener] = []
        self._terminal_condition = threading.Condition()
        self._goal_context_provider: GoalContextProvider | None = None

    def configure_goal_runtime(
        self,
        handler: GoalRuntimeHandler,
        *,
        context_provider: GoalContextProvider | None = None,
    ) -> None:
        self.session_runtimes.configure_goal_handler(handler)
        self._goal_context_provider = context_provider

    def add_settled_listener(self, listener: TurnSettledListener) -> None:
        """Observe fully persisted terminal Turns.

        Listeners run after queued user work has been scheduled and must return
        quickly. Extensions use this seam only after the Turn transaction has
        committed.
        """

        with self._settled_listener_lock:
            if listener not in self._settled_listeners:
                self._settled_listeners.append(listener)

    def subscribe_thread_events(
        self,
        thread_id: str,
        observer: Callable[[Event], None],
    ) -> str:
        """Observe every live SQ/EQ event for future Turns in one Thread.

        Frontends use this Session-scoped subscription for ordinary, queued,
        Goal, retry, and automatic continuation Turns alike. Persistence and
        execution remain independent of the best-effort observer.
        """

        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
        token = uuid4().hex
        with self._observer_lock:
            self._thread_event_observers[token] = (thread_id, observer)
        return token

    def unsubscribe_thread_events(self, token: str) -> None:
        with self._observer_lock:
            self._thread_event_observers.pop(token, None)

    def remove_settled_listener(self, listener: TurnSettledListener) -> None:
        with self._settled_listener_lock:
            if listener in self._settled_listeners:
                self._settled_listeners.remove(listener)

    def start(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        event_observer: Callable[[Event], None] | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        input_source: TurnInputSource = TurnInputSource.START,
    ) -> TurnSnapshot:
        association = self._goal_association(thread_id)
        return self._submit(
            thread_id,
            prompt=prompt,
            skill_ids=self._merge_goal_skills(skill_ids, association),
            connection_id=connection_id,
            model=model,
            reasoning_effort=reasoning_effort,
            queue_if_busy=False,
            event_observer=event_observer,
            input_message_id=message_id,
            client_surface=client_surface,
            input_source=input_source,
            input_delivery=TurnInputDelivery.CURRENT_TURN,
            goal_id=association.goal_id if association is not None else None,
        ).snapshot

    def enqueue(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        event_observer: Callable[[Event], None] | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> TurnSnapshot:
        """Persist a next Turn and run it after earlier Turns settle."""

        association = self._goal_association(thread_id)
        return self._submit(
            thread_id,
            prompt=prompt,
            skill_ids=self._merge_goal_skills(skill_ids, association),
            connection_id=connection_id,
            model=model,
            reasoning_effort=reasoning_effort,
            queue_if_busy=True,
            event_observer=event_observer,
            input_message_id=message_id,
            client_surface=client_surface,
            input_source=TurnInputSource.QUEUE,
            input_delivery=TurnInputDelivery.NEXT_TURN,
            goal_id=association.goal_id if association is not None else None,
        ).snapshot

    def _submit(
        self,
        thread_id: str,
        *,
        prompt: str,
        skill_ids: tuple[str, ...],
        connection_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        queue_if_busy: bool,
        event_observer: Callable[[Event], None] | None,
        execution_profile_override: ExecutionProfile | None = None,
        goal_id: str | None = None,
        input_message_id: str | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        input_source: TurnInputSource = TurnInputSource.START,
        input_delivery: TurnInputDelivery = TurnInputDelivery.CURRENT_TURN,
    ) -> _TurnSubmission:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise EmptyInputError("turn prompt must not be empty")
        try:
            clean_skill_ids = tuple(
                SkillSelection(skill_id=skill_id).skill_id for skill_id in skill_ids
            )
            if len(set(clean_skill_ids)) != len(clean_skill_ids):
                raise ValueError("skill IDs must be unique")
            if len(clean_skill_ids) > MAX_SELECTED_SKILLS:
                raise ValueError(
                    f"a turn may select at most {MAX_SELECTED_SKILLS} Skills"
                )
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
            items = ItemRepository(connection)
            if input_message_id is not None:
                input_message_id = input_message_id.strip()
                if not input_message_id:
                    raise EmptyInputError("messageId must not be empty")
                existing_item = items.find_user_message_by_message_id(
                    thread_id,
                    input_message_id,
                )
                if existing_item is not None:
                    if (
                        existing_item.payload.get("text") != clean_prompt
                        or existing_item.payload.get("source") != input_source.value
                    ):
                        raise DuplicateMessageConflictError(
                            "messageId was already used with different content"
                        )
                    existing_turn = turns.get(existing_item.turn_id)
                    if existing_turn is None:
                        raise DuplicateMessageConflictError(
                            "idempotent input references a missing Turn"
                        )
                    return _TurnSubmission(
                        TurnSnapshot(
                            existing_turn,
                            tuple(items.list_for_turn(existing_turn.id)),
                            tuple(
                                ApprovalRepository(connection).list_for_turn(
                                    existing_turn.id
                                )
                            ),
                        ),
                        duplicate=True,
                    )
            active = turns.active_for_thread(thread_id)
            if active is not None and not queue_if_busy:
                raise TurnAlreadyRunningError(
                    f"thread already has an active Turn: {active.id}",
                    details={
                        "threadId": thread_id,
                        "actualTurnId": active.id,
                    },
                )
            schedule_now = active is None
            execution_profile = execution_profile_override
            if execution_profile is None:
                execution_profile = self.llm_configuration.resolve(
                    thread.workspace_path,
                    ExecutionSelection(
                        connection_id=connection_id or thread.connection_id,
                        model_id=model or thread.model,
                        reasoning_effort=(
                            reasoning_effort
                            if reasoning_effort is not None
                            else thread.reasoning_effort
                        ),
                    ),
                )
            turn = Turn(
                thread_id=thread_id,
                ordinal=turns.next_ordinal(thread_id),
                prompt=clean_prompt,
                skill_ids=clean_skill_ids,
                execution_profile=execution_profile,
                goal_id=goal_id,
            )
            turns.add(turn)
            now = utc_now()
            input_metadata = {
                "client": client_surface.value,
                "delivery": input_delivery.value,
                "source": input_source.value,
                **(
                    {"messageId": input_message_id}
                    if input_message_id is not None
                    else {}
                ),
            }
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
                    "executionProfile": execution_profile.to_dict(),
                    **input_metadata,
                    "goal": ({"id": goal_id} if goal_id is not None else None),
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
            if input_message_id is not None:
                events.append(
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn.id,
                        item_id=user_item.id,
                        type=(
                            "turn.input_queued"
                            if input_delivery is TurnInputDelivery.NEXT_TURN
                            else "turn.input_started"
                        ),
                        payload={
                            "turnId": turn.id,
                            "messageId": input_message_id,
                            "delivery": input_delivery.value,
                        },
                    )
                )
        self._publish(events)
        if event_observer is not None:
            with self._observer_lock:
                self._event_observers[turn.id] = event_observer
        if schedule_now:
            self._schedule(turn.id)
        return _TurnSubmission(TurnSnapshot(turn, (user_item,), ()))

    def retry(
        self,
        turn_id: str,
        *,
        use_current_selection: bool = False,
    ) -> TurnSnapshot:
        original = self.read(turn_id).turn
        if not original.status.is_terminal:
            raise ConflictError(
                "only a completed, failed, or interrupted Turn can retry"
            )
        association = self._goal_association(original.thread_id)
        return self._submit(
            original.thread_id,
            prompt=original.prompt,
            skill_ids=self._merge_goal_skills(original.skill_ids, association),
            connection_id=None,
            model=None,
            reasoning_effort=None,
            queue_if_busy=False,
            event_observer=None,
            execution_profile_override=(
                None if use_current_selection else original.execution_profile
            ),
            goal_id=association.goal_id if association is not None else None,
            client_surface=ClientSurface.INTERNAL,
            input_source=TurnInputSource.RETRY,
        ).snapshot

    def _goal_association(self, thread_id: str) -> GoalTurnAssociation | None:
        provider = self._goal_context_provider
        return provider(thread_id) if provider is not None else None

    @staticmethod
    def _merge_goal_skills(
        explicit: tuple[str, ...],
        association: GoalTurnAssociation | None,
    ) -> tuple[str, ...]:
        if association is None or not association.skill_ids:
            return explicit
        merged = tuple(dict.fromkeys((*explicit, *association.skill_ids)))
        if len(merged) > MAX_SELECTED_SKILLS:
            raise InvalidArgumentError(
                "the selected Turn and active Goal Skills exceed "
                f"the {MAX_SELECTED_SKILLS}-Skill limit"
            )
        return merged

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

    def active_for_thread(self, thread_id: str) -> Turn | None:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            return TurnRepository(connection).active_for_thread(thread_id)

    def executing_for_thread(self, thread_id: str) -> Turn | None:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            return TurnRepository(connection).executing_for_thread(thread_id)

    def may_resume_queued_after_restart(self, turn: Turn) -> bool:
        """Resume user Queue, never an automatic Goal continuation."""

        if turn.goal_id is None:
            return True
        with self.database.read() as connection:
            return any(
                item.kind is ItemKind.USER_MESSAGE
                and item.payload.get("source") == "queue"
                for item in ItemRepository(connection).list_for_turn(turn.id)
            )

    def steer(
        self,
        thread_id: str,
        *,
        expected_turn_id: str,
        prompt: str,
        message_id: str,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> TurnInputReceipt:
        """Deliver a follow-up to exactly one executing Turn."""

        return self.turn_inputs.steer(
            thread_id,
            expected_turn_id=expected_turn_id,
            prompt=prompt,
            message_id=message_id,
            client_surface=client_surface,
        )

    def wait_until_terminal(
        self,
        turn_id: str,
        *,
        timeout: float = 5.0,
    ) -> Turn | None:
        """Wait for one final-closing Turn without polling or changing it."""

        if timeout < 0:
            raise ValueError("timeout must not be negative")
        deadline = time.monotonic() + timeout
        with self._terminal_condition:
            while True:
                current = self.read(turn_id).turn
                if current.status.is_terminal:
                    return current
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._terminal_condition.wait(remaining)

    def inject_goal_update(
        self,
        turn_id: str,
        *,
        message_id: str,
        goal_id: str,
        objective: str,
    ) -> bool:
        return self.turn_inputs.inject_goal_update(
            turn_id,
            message_id=message_id,
            goal_id=goal_id,
            objective=objective,
        )

    def conversation_count(self, thread_id: str) -> int:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            return ItemRepository(connection).conversation_count(thread_id)

    def clear_live_context(self, thread_id: str) -> None:
        """Clear resident model context without rewriting canonical Session data."""

        if self.active_for_thread(thread_id) is not None:
            raise ConflictError("cannot clear context while a Turn is active")
        self.session_runtimes.clear_live_history(thread_id)

    def interrupt(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout: float = 5.0,
    ) -> tuple[bool, Turn]:
        """Interrupt exactly one Turn and wait for its durable terminal state."""

        active = self.active_for_thread(thread_id)
        snapshot = self.read(turn_id)
        if snapshot.turn.thread_id != thread_id:
            raise ExpectedTurnMismatchError(
                turn_id,
                active.id if active is not None else None,
            )
        if snapshot.turn.status.is_terminal:
            return False, snapshot.turn
        accepted = self.registry.interrupt(turn_id)
        if not accepted and snapshot.turn.status is TurnStatus.QUEUED:
            try:
                self._finish_unstarted(
                    turn_id,
                    status=TurnStatus.INTERRUPTED,
                    stop_reason="interrupted",
                )
            finally:
                self._remove_observer(turn_id)
            accepted = True
        if not accepted:
            return False, self.read(turn_id).turn
        deadline = time.monotonic() + timeout
        with self._terminal_condition:
            while True:
                current = self.read(turn_id).turn
                if current.status.is_terminal:
                    return True, current
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TurnInterruptTimeoutError(
                        f"Turn did not stop within {timeout:g} seconds",
                        details={"threadId": thread_id, "turnId": turn_id},
                    )
                self._terminal_condition.wait(remaining)

    async def _execute(self, turn_id: str) -> None:
        projection: TurnEventProjector | None = None
        session_thread_id: str | None = None
        session_acquired = False
        status = TurnStatus.FAILED
        stop_reason = "protocol_incomplete"
        error_code: str | None = "PROTOCOL_INCOMPLETE"
        error_message: str | None = "agent stream ended without task_complete"
        turn_usage: dict[str, int] = {}
        try:
            turn, workspace, execution_profile = self._mark_running(turn_id)
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
                model=execution_profile.model_id,
                execution_profile=execution_profile,
                approval_callback=approve,
            )
            session_acquired = True
            self.session_runtimes.prepare_inputs(
                turn.thread_id,
                turn_id=turn.id,
            )
            skill_invocations: dict[str, SkillInvocation] = {}
            stored_user = False
            inputs_active = False
            goal_metadata = {"goalId": turn.goal_id} if turn.goal_id is not None else {}
            initial_item = next(
                (
                    item
                    for item in self.read(turn.id).items
                    if item.kind is ItemKind.USER_MESSAGE and item.ordinal == 1
                ),
                None,
            )
            initial_input_metadata = (
                {
                    key: initial_item.payload[key]
                    for key in ("messageId", "client", "delivery", "source")
                    if key in initial_item.payload
                }
                if initial_item is not None
                else {}
            )
            turn_client = _client_surface_value(initial_input_metadata.get("client"))
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
                                "schemaVersion": 3,
                                "client": turn_client,
                                "turnId": turn.id,
                                **initial_input_metadata,
                                "executionProfile": execution_profile.to_dict(),
                                **goal_metadata,
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
                    if not inputs_active:
                        if turn.goal_id is not None:
                            self.session_runtimes.activate_goal(
                                turn.thread_id,
                                context=GoalRuntimeContext(
                                    thread_id=turn.thread_id,
                                    goal_id=turn.goal_id,
                                    turn_id=turn.id,
                                ),
                            )
                        self.session_runtimes.activate_inputs(
                            turn.thread_id,
                            turn_id=turn.id,
                        )
                        inputs_active = True
                elif isinstance(event.msg, SkillLoaded):
                    invocation = event.msg.invocation
                    skill_invocations[invocation.skill_id] = invocation
                self._notify_observer(turn_id, turn.thread_id, event)
                projection.project(event)
            raw_usage = getattr(session, "last_usage", {})
            if not projection.usage:
                turn_usage = normalize_usage(raw_usage)
            if projection.saw_terminal:
                if projection.final_text:
                    continuation_metadata = assistant_continuation_metadata(
                        getattr(session, "history", ())
                    )
                    stored_assistant = self.session_store.append_message(
                        turn.thread_id,
                        "assistant",
                        projection.final_text,
                        metadata={
                            "schemaVersion": 3,
                            "client": turn_client,
                            "turnId": turn.id,
                            "executionProfile": execution_profile.to_dict(),
                            **continuation_metadata,
                            **goal_metadata,
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
                self.session_runtimes.release(
                    session_thread_id,
                    turn_id=turn_id,
                )

        if status is TurnStatus.INTERRUPTED:
            self._record_interruption_marker(self.read(turn_id).turn)

        if projection is not None:
            try:
                turn_usage = projection.usage or turn_usage
                projection.close_open_items(
                    interrupted=status is TurnStatus.INTERRUPTED
                )
                projection.add_completion(
                    status=ItemStatus.COMPLETED
                    if status is TurnStatus.COMPLETED
                    else ItemStatus.FAILED,
                    stop_reason=stop_reason,
                    usage=turn_usage,
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

    def _record_interruption_marker(self, turn: Turn) -> None:
        """Persist one model-visible abort fact before terminal Turn events."""

        session = self.session_store.get_session(turn.thread_id)
        if session is None:
            raise ThreadNotFoundError(
                f"canonical session disappeared: {turn.thread_id}"
            )
        if any(
            message.metadata.get("source") == TurnInputSource.TURN_INTERRUPT.value
            and message.metadata.get("turnId") == turn.id
            for message in session.messages
        ):
            return
        stored = self.session_store.append_message(
            turn.thread_id,
            "user",
            "[The previous Turn was interrupted before it completed.]",
            metadata={
                "schemaVersion": 3,
                "client": ClientSurface.INTERNAL.value,
                "turnId": turn.id,
                "source": TurnInputSource.TURN_INTERRUPT.value,
                "modelVisible": True,
            },
        )
        if stored is None:
            raise ThreadNotFoundError(
                f"canonical session disappeared: {turn.thread_id}"
            )
        # The live Agent has not seen this marker. Deliberately leave its
        # canonical count stale so the next acquire reloads authoritative
        # Session history before another Turn starts.

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

    def _notify_observer(
        self,
        turn_id: str,
        thread_id: str,
        event: Event,
    ) -> None:
        with self._observer_lock:
            observers = [
                self._event_observers.get(turn_id),
                *(
                    observer
                    for observed_thread_id, observer in self._thread_event_observers.values()
                    if observed_thread_id == thread_id
                ),
            ]
        seen: set[int] = set()
        for observer in observers:
            if observer is None or id(observer) in seen:
                continue
            seen.add(id(observer))
            try:
                observer(event)
            except Exception:
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

    def discard_session_runtime(self, session_id: str) -> None:
        """Release an idle Agent runtime whose canonical Session was deleted."""

        if session_id not in self.session_runtimes.live_session_ids:
            return
        self.registry.run_maintenance(lambda: self.session_runtimes.discard(session_id))

    def _mark_running(self, turn_id: str) -> tuple[Turn, str, ExecutionProfile]:
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
            profile = running.execution_profile
            if profile is None:
                profile = self.llm_configuration.resolve(
                    thread.workspace_path,
                    ExecutionSelection(
                        connection_id=thread.connection_id,
                        model_id=thread.model,
                        reasoning_effort=thread.reasoning_effort,
                    ),
                )
                running = replace(running, execution_profile=profile)
                turns.update(running)
            event = EventRepository(connection).append(
                thread_id=turn.thread_id,
                turn_id=turn.id,
                type="turn.updated",
                payload={"turn": turn_view(running)},
            )
        self.broker.publish(event)
        return running, thread.workspace_path, profile

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
        with self._terminal_condition:
            self._terminal_condition.notify_all()
        if schedule_next_id is not None:
            self._schedule(schedule_next_id, propagate=False)
        self._notify_settled(terminal)
        return terminal

    def _notify_settled(self, turn: Turn) -> None:
        with self._settled_listener_lock:
            listeners = tuple(self._settled_listeners)
        for listener in listeners:
            try:
                listener(turn)
            except Exception:
                logging.getLogger(__name__).exception(
                    "turn settled listener failed for %s",
                    turn.id,
                )

    def recover_incomplete(
        self,
        *,
        resume_queued: Callable[[Turn], bool] | None = None,
    ) -> int:
        """Interrupt lost live work and resume explicitly safe queued Turns.

        Ordinary user-queued Turns remain resumable by default. Product-level
        extensions may reject a queued Turn when replaying it after a process
        crash could repeat unknown mutations.
        """

        events: list[DomainEvent] = []
        schedule_ids: list[str] = []
        recovered_turns: list[Turn] = []
        should_resume = resume_queued or (lambda _turn: True)
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            items = ItemRepository(connection)
            approvals = ApprovalRepository(connection)
            threads = ThreadRepository(connection)
            event_repo = EventRepository(connection)
            active_turns = turns.list_active()
            affected_threads = {turn.thread_id for turn in active_turns}
            for turn in active_turns:
                if turn.status is TurnStatus.QUEUED and should_resume(turn):
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
                recorded_usage = aggregate_recorded_usage(
                    event_repo.list_for_turn(
                        turn.thread_id,
                        turn.id,
                        event_type=TURN_USAGE_EVENT_TYPE,
                    )
                )
                completion = Item(
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    ordinal=items.next_ordinal(turn.id),
                    kind=ItemKind.COMPLETION,
                    status=ItemStatus.FAILED,
                    summary="Turn interrupted after application restart",
                    payload={
                        "stopReason": "application_restarted",
                        **({"usage": recorded_usage} if recorded_usage else {}),
                    },
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
                recovered_turns.append(interrupted)
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
        for turn in recovered_turns:
            self._notify_settled(turn)
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


def _client_surface_value(value: object) -> str:
    try:
        return ClientSurface(value).value
    except (TypeError, ValueError):
        return ClientSurface.INTERNAL.value
