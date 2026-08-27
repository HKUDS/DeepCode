"""Durable turn orchestration over the shared AgentSession kernel."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Generic, TypeVar
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
from core.application.execution_coordinator import (
    ExecutionCoordinator,
    ExecutionDispatch,
    OrphanedExecution,
)
from core.application.execution_registry import ExecutionRegistry
from core.application.execution_security_policy import ExecutionSecurityPolicy
from core.application.goal_turn_port import (
    GoalContextProvider,
    GoalSubmissionScope,
    GoalTurnAssociation,
)
from core.application.llm_configuration_service import LLMConfigurationService
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
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.execution_security import (
    ExecutionSecurityProfile,
    parse_access_preset_override,
)
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import (
    ClientSurface,
    TurnInputDelivery,
    TurnInputSource,
)
from core.domain.project import TrustState
from core.domain.runtime_coordination import ExecutionClass, ResourceClaim
from core.domain.thread import Thread, ThreadStatus
from core.domain.turn import Turn, TurnExecutor, TurnStatus
from core.events import Event, SkillLoaded, TurnStarted, UserInput
from core.persistence.coordination_repository import RuntimeCoordinationRepository
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import (
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
    TurnWriteConflictError,
)
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore
from core.sessions.continuation import assistant_continuation_metadata
from core.skills.host import SkillWorkspaceRegistry
from core.skills.models import MAX_SELECTED_SKILLS, SkillInvocation, SkillSelection

TurnSettledListener = Callable[[Turn], None]
ParticipantResultT = TypeVar("ParticipantResultT")
_DURABLE_STATE_POLL_INTERVAL = 0.1


@dataclass(frozen=True, slots=True)
class TurnAdmissionContext:
    """One proposed Turn, inspected inside its write transaction.

    ``reservation_id`` is an internal, unforgeable-by-transport capability
    supplied by an owning application service. Admission guards may use the
    shared connection to enforce durable ownership without teaching
    ``TurnService`` about Automation, Workflow, or any future owner type.
    """

    connection: sqlite3.Connection
    thread: Thread
    goal_id: str | None
    goal_turn_settlement_ids: frozenset[str]
    input_source: TurnInputSource
    reservation_id: str | None
    queue_if_busy: bool


TurnAdmissionGuard = Callable[[TurnAdmissionContext], None]


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    turn: Turn
    items: tuple[Item, ...]
    approvals: tuple[Approval, ...]


@dataclass(frozen=True, slots=True)
class TurnTransactionContext:
    """Core records visible to a participant inside the Turn transaction.

    The connection is valid only for the duration of the participant callback.
    Participants must use it for every durable sidecar write so an exception can
    roll back the complete submission.
    """

    connection: sqlite3.Connection
    turn: Turn
    user_item: Item
    thread: Thread


@dataclass(frozen=True, slots=True)
class TurnTransactionContribution(Generic[ParticipantResultT]):
    """A participant value plus DomainEvents already appended in its transaction."""

    value: ParticipantResultT
    events: tuple[DomainEvent, ...] = ()


TurnTransactionParticipant = Callable[
    [TurnTransactionContext],
    TurnTransactionContribution[ParticipantResultT],
]


@dataclass(frozen=True, slots=True)
class TurnSubmissionResult(Generic[ParticipantResultT]):
    """A committed Turn snapshot and its durable participant result."""

    snapshot: TurnSnapshot
    participant_result: ParticipantResultT


@dataclass(frozen=True, slots=True)
class _TurnSubmission(Generic[ParticipantResultT]):
    snapshot: TurnSnapshot
    participant: TurnTransactionContribution[ParticipantResultT] | None = None
    duplicate: bool = False
    events: tuple[DomainEvent, ...] = ()
    schedule_now: bool = False
    event_observer: Callable[[Event], None] | None = None


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
        holder_label: str | None = None,
        llm_configuration: LLMConfigurationService | None = None,
        execution_security_policy: ExecutionSecurityPolicy | None = None,
        skill_hosts: SkillWorkspaceRegistry | None = None,
    ) -> None:
        self.database = database
        self.broker = broker
        self.approvals = approvals
        self.registry = registry
        self.session_factory = session_factory or DefaultAgentSessionFactory()
        self.session_store = session_store
        self.llm_configuration = llm_configuration or LLMConfigurationService()
        self.execution_security_policy = (
            execution_security_policy or ExecutionSecurityPolicy()
        )
        # One string shared with the registry, so "is that holder me?" is
        # exact equality rather than pid parsing.
        self.holder_label = holder_label or (
            f"another DeepCode process (pid {os.getpid()})"
        )
        self.session_runtimes = SessionRuntimeRegistry(
            session_store,
            self.session_factory,
            skill_hosts=skill_hosts,
            holder_label=self.holder_label,
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
        self._admission_guard_lock = threading.Lock()
        self._admission_guards: list[TurnAdmissionGuard] = []
        self._terminal_condition = threading.Condition()
        self._goal_context_provider: GoalContextProvider | None = None
        self._goal_submission_scope: GoalSubmissionScope | None = None
        self._execution_coordinator: ExecutionCoordinator | None = None

    def configure_execution_coordinator(
        self,
        coordinator: ExecutionCoordinator,
    ) -> None:
        """Route future product Turns through one durable admission worker."""

        if self._execution_coordinator is not None:
            raise RuntimeError("execution coordinator is already configured")
        if coordinator.database.path != self.database.path:
            raise ValueError("execution coordinator must share the Turn database")
        self._execution_coordinator = coordinator

    def configure_goal_runtime(
        self,
        handler: GoalRuntimeHandler,
        *,
        context_provider: GoalContextProvider | None = None,
        submission_scope: GoalSubmissionScope | None = None,
    ) -> None:
        self.session_runtimes.configure_goal_handler(handler)
        self._goal_context_provider = context_provider
        self._goal_submission_scope = submission_scope

    def add_settled_listener(self, listener: TurnSettledListener) -> None:
        """Observe fully persisted terminal Turns.

        Listeners run after queued user work has been scheduled and must return
        quickly. Extensions use this seam only after the Turn transaction has
        committed.
        """

        with self._settled_listener_lock:
            if listener not in self._settled_listeners:
                self._settled_listeners.append(listener)

    def add_admission_guard(self, guard: TurnAdmissionGuard) -> None:
        """Register an owner-neutral guard for future Turn submissions."""

        with self._admission_guard_lock:
            if guard not in self._admission_guards:
                self._admission_guards.append(guard)

    def remove_admission_guard(self, guard: TurnAdmissionGuard) -> None:
        with self._admission_guard_lock:
            if guard in self._admission_guards:
                self._admission_guards.remove(guard)

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
        expected_goal_id: str | None = None,
        execution_class: ExecutionClass | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        execution_permission_mode: ExecutionPermissionMode | None = None,
    ) -> TurnSnapshot:
        with self._goal_submission(thread_id) as association:
            self._require_goal_association(association, expected_goal_id)
            submission = self._submit(
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
                execution_class=(execution_class or _execution_class_for(input_source)),
                execution_security_profile_override=execution_security_profile,
                execution_permission_mode_override=execution_permission_mode,
                goal_id=association.goal_id if association is not None else None,
                goal_turn_settlement_ids=(
                    association.turn_settlement_ids
                    if association is not None
                    else frozenset()
                ),
            )
        return self._activate_submission(submission).snapshot

    def start_with_participant(
        self,
        thread_id: str,
        *,
        prompt: str,
        participant: TurnTransactionParticipant[ParticipantResultT],
        reservation_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        event_observer: Callable[[Event], None] | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        input_source: TurnInputSource = TurnInputSource.START,
        expected_goal_id: str | None = None,
        execution_class: ExecutionClass | None = None,
        execution_security_profile: ExecutionSecurityProfile | None = None,
        execution_permission_mode: ExecutionPermissionMode | None = None,
    ) -> TurnSubmissionResult[ParticipantResultT]:
        """Start a Turn and atomically persist one owner-provided sidecar.

        The participant runs after the Turn, initial user Item, running Thread,
        and their events have been written, but before the transaction commits.
        It must perform all writes through ``context.connection`` and return any
        DomainEvents appended with that connection. TurnService publishes both
        core and participant events only after the shared commit. Any participant
        exception rolls back the entire submission and prevents scheduling.
        """

        with self._goal_submission(thread_id) as association:
            self._require_goal_association(association, expected_goal_id)
            submission = self._submit(
                thread_id,
                prompt=prompt,
                skill_ids=self._merge_goal_skills(skill_ids, association),
                connection_id=connection_id,
                model=model,
                reasoning_effort=reasoning_effort,
                queue_if_busy=False,
                event_observer=event_observer,
                client_surface=client_surface,
                input_source=input_source,
                input_delivery=TurnInputDelivery.CURRENT_TURN,
                execution_class=(execution_class or _execution_class_for(input_source)),
                execution_security_profile_override=execution_security_profile,
                execution_permission_mode_override=execution_permission_mode,
                goal_id=association.goal_id if association is not None else None,
                goal_turn_settlement_ids=(
                    association.turn_settlement_ids
                    if association is not None
                    else frozenset()
                ),
                reservation_id=reservation_id,
                transaction_participant=participant,
            )
        submission = self._activate_submission(submission)
        contribution = submission.participant
        if contribution is None:
            raise AssertionError("Turn transaction participant did not run")
        return TurnSubmissionResult(
            snapshot=submission.snapshot,
            participant_result=contribution.value,
        )

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

        with self._goal_submission(thread_id) as association:
            submission = self._submit(
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
                execution_class=ExecutionClass.INTERACTIVE,
                goal_id=association.goal_id if association is not None else None,
                goal_turn_settlement_ids=(
                    association.turn_settlement_ids
                    if association is not None
                    else frozenset()
                ),
            )
        return self._activate_submission(submission).snapshot

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
        execution_security_profile_override: ExecutionSecurityProfile | None = None,
        execution_permission_mode_override: ExecutionPermissionMode | None = None,
        goal_id: str | None = None,
        goal_turn_settlement_ids: frozenset[str] = frozenset(),
        input_message_id: str | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        input_source: TurnInputSource = TurnInputSource.START,
        input_delivery: TurnInputDelivery = TurnInputDelivery.CURRENT_TURN,
        execution_class: ExecutionClass = ExecutionClass.INTERACTIVE,
        reservation_id: str | None = None,
        transaction_participant: (
            TurnTransactionParticipant[ParticipantResultT] | None
        ) = None,
    ) -> _TurnSubmission[ParticipantResultT]:
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
        participant_contribution: (
            TurnTransactionContribution[ParticipantResultT] | None
        ) = None
        with self.database.transaction() as connection:
            threads = ThreadRepository(connection)
            thread = threads.get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            # JSONL Session metadata is canonical. A prior metadata update may
            # have committed before its disposable SQLite projection failed,
            # so admission revalidates the security override and repairs the
            # projection inside the same transaction that creates the Turn.
            canonical_session = self.session_store.get_session(thread_id)
            if canonical_session is not None:
                try:
                    canonical_access = parse_access_preset_override(
                        canonical_session.metadata or {}
                    )
                except ValueError as exc:
                    raise ConflictError(
                        "canonical Session contains an invalid access preset override"
                    ) from exc
                if canonical_access is not thread.access_preset_override:
                    thread = replace(
                        thread,
                        access_preset_override=canonical_access,
                        updated_at=utc_now(),
                    )
                    threads.update(thread)
            if thread.status is ThreadStatus.ARCHIVED:
                raise ConflictError("cannot start a turn in an archived thread")
            project = ProjectRepository(connection).get(thread.project_id)
            if project is None:
                raise ConflictError("thread project is missing")
            if project.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must be trusted before agent execution"
                )
            self._refuse_if_running_elsewhere(thread_id)
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
            with self._admission_guard_lock:
                admission_guards = tuple(self._admission_guards)
            admission = TurnAdmissionContext(
                connection=connection,
                thread=thread,
                goal_id=goal_id,
                goal_turn_settlement_ids=goal_turn_settlement_ids,
                input_source=input_source,
                reservation_id=reservation_id,
                queue_if_busy=queue_if_busy,
            )
            for guard in admission_guards:
                guard(admission)
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
            goal_turns = (
                turns.list_for_goal(thread_id, goal_id) if goal_id is not None else ()
            )
            execution_security_profile = self.execution_security_policy.resolve(
                thread,
                explicit_profile=execution_security_profile_override,
                explicit_permission_mode=execution_permission_mode_override,
                goal_turns=goal_turns,
            )
            execution_permission_mode = execution_security_profile.permission_mode
            turn = Turn(
                thread_id=thread_id,
                ordinal=turns.next_ordinal(thread_id),
                prompt=clean_prompt,
                skill_ids=clean_skill_ids,
                execution_profile=execution_profile,
                execution_permission_mode=execution_permission_mode,
                execution_security_profile=execution_security_profile,
                goal_id=goal_id,
                execution_class=execution_class,
                home_worker_id=self._submitting_worker_id(),
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
                    "executionSecurityProfile": (execution_security_profile.to_dict()),
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
            if transaction_participant is not None:
                participant_contribution = transaction_participant(
                    TurnTransactionContext(
                        connection=connection,
                        turn=turn,
                        user_item=user_item,
                        thread=running_thread,
                    )
                )
                if not isinstance(
                    participant_contribution,
                    TurnTransactionContribution,
                ):
                    raise TypeError(
                        "Turn transaction participant must return "
                        "TurnTransactionContribution"
                    )
                events.extend(participant_contribution.events)
        return _TurnSubmission(
            TurnSnapshot(turn, (user_item,), ()),
            participant=participant_contribution,
            events=tuple(events),
            schedule_now=schedule_now,
            event_observer=event_observer,
        )

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
        with self._goal_submission(original.thread_id) as association:
            submission = self._submit(
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
                # A retry is a new Turn. Model selection may be replayed for
                # reproducibility, but an old Full Access grant must never be
                # replayed after the Session has been downgraded. The admission
                # policy resolves current Session access; Automation-owned Goals
                # still inherit their authoritative owner policy there.
                execution_security_profile_override=None,
                execution_permission_mode_override=None,
                goal_id=association.goal_id if association is not None else None,
                goal_turn_settlement_ids=(
                    association.turn_settlement_ids
                    if association is not None
                    else frozenset()
                ),
                client_surface=ClientSurface.INTERNAL,
                input_source=TurnInputSource.RETRY,
                execution_class=ExecutionClass.INTERACTIVE,
            )
        return self._activate_submission(submission).snapshot

    def _activate_submission(
        self,
        submission: _TurnSubmission[ParticipantResultT],
    ) -> _TurnSubmission[ParticipantResultT]:
        """Publish and schedule only after every admission lock is released."""

        if submission.duplicate:
            return submission
        turn = submission.snapshot.turn
        self._publish(submission.events)
        if submission.event_observer is not None:
            with self._observer_lock:
                self._event_observers[turn.id] = submission.event_observer
        if submission.schedule_now:
            self._schedule(turn.id)
        return submission

    def _goal_association(self, thread_id: str) -> GoalTurnAssociation | None:
        provider = self._goal_context_provider
        return provider(thread_id) if provider is not None else None

    @staticmethod
    def _require_goal_association(
        association: GoalTurnAssociation | None,
        expected_goal_id: str | None,
    ) -> None:
        if expected_goal_id is None:
            return
        actual_goal_id = association.goal_id if association is not None else None
        if actual_goal_id != expected_goal_id:
            raise ConflictError(
                "Goal association changed before Turn submission",
                details={
                    "expectedGoalId": expected_goal_id,
                    "actualGoalId": actual_goal_id,
                },
            )

    @contextmanager
    def _goal_submission(
        self,
        thread_id: str,
    ) -> Iterator[GoalTurnAssociation | None]:
        scope = self._goal_submission_scope
        if scope is None:
            yield self._goal_association(thread_id)
            return
        with scope(thread_id) as association:
            yield association

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
        coordinator = self._execution_coordinator
        if coordinator is not None and coordinator.worker is not None:
            coordinator.offer()
            return
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

    def start_claimed_execution(self, dispatch: ExecutionDispatch) -> None:
        """Hand one fenced admission to the existing Agent execution runtime."""

        coordinator = self._require_execution_coordinator()
        if dispatch.executor is not TurnExecutor.AGENT:
            raise TurnWriteConflictError(
                f"Agent handler received {dispatch.executor.value} Turn "
                f"{dispatch.turn_id}"
            )
        if dispatch.claim.worker_id != coordinator.worker_id:
            raise TurnWriteConflictError(
                f"Turn claim belongs to another worker: {dispatch.turn_id}"
            )
        turn = self.read(dispatch.turn_id).turn
        self.session_runtimes.prepare_inputs(
            turn.thread_id,
            turn_id=turn.id,
        )
        try:
            self.registry.start(
                dispatch.turn_id,
                lambda: self._execute(dispatch.turn_id, claim=dispatch.claim),
                on_cancelled_before_start=lambda: self._cancel_before_start(
                    dispatch.turn_id,
                    claim=dispatch.claim,
                ),
            )
        except BaseException:
            self.session_runtimes.release(
                turn.thread_id,
                turn_id=turn.id,
            )
            raise

    def fail_claimed_start(
        self,
        dispatch: ExecutionDispatch,
        error: Exception,
    ) -> None:
        """Settle registry admission failure without exposing the Turn to retry."""

        thread_id: str | None = None
        try:
            thread_id = self.read(dispatch.turn_id).turn.thread_id
            self._finish_unstarted(
                dispatch.turn_id,
                status=TurnStatus.FAILED,
                stop_reason="scheduler_error",
                error_code="SCHEDULER_ERROR",
                error_message=str(error),
                claim=dispatch.claim,
            )
        finally:
            if thread_id is not None:
                self.session_runtimes.release(
                    thread_id,
                    turn_id=dispatch.turn_id,
                )
            self._remove_observer(dispatch.turn_id)

    def cancel_claimed_execution(self, claim: ResourceClaim) -> None:
        """Deliver a durable cancel request only to the claim-owning runtime."""

        coordinator = self._require_execution_coordinator()
        if claim.worker_id != coordinator.worker_id or claim.turn_id is None:
            raise TurnWriteConflictError("cancellation claim belongs to another worker")
        if self.registry.interrupt(claim.turn_id):
            return
        turn = self.read(claim.turn_id).turn
        if turn.status.is_terminal:
            return
        if turn.status is TurnStatus.QUEUED:
            try:
                self._finish_unstarted(
                    claim.turn_id,
                    status=TurnStatus.INTERRUPTED,
                    stop_reason="interrupted",
                    claim=claim,
                )
            finally:
                self._remove_observer(claim.turn_id)
            return
        raise ConflictError(
            "claim-owning runtime could not interrupt an executing Turn",
            details={
                "turnId": claim.turn_id,
                "status": turn.status.value,
            },
        )

    def recover_orphaned_execution(self, orphan: OrphanedExecution) -> None:
        """Interrupt dead-worker execution while its OS-death proof is held."""

        if orphan.status not in {
            TurnStatus.RUNNING.value,
            TurnStatus.WAITING_APPROVAL.value,
        }:
            raise ValueError(f"cannot recover orphan status: {orphan.status}")
        self._finish(
            orphan.turn_id,
            status=TurnStatus.INTERRUPTED,
            stop_reason="worker_crashed",
            claim=orphan.claim,
            ensure_completion=True,
            recover_active=True,
            terminal_event_type="turn.recovered",
        )

    def _refuse_if_running_elsewhere(self, thread_id: str) -> None:
        """Fail a submission fast when another PROCESS is executing here.

        The authoritative gate is the run lease taken around execution
        (`SessionRuntimeRegistry.acquire`), but by then the Turn is already
        created and marked running, so a refusal there surfaces as a failed
        Turn. Probing at submission turns the common case into a clean
        refusal before anything exists. Our own process holding the lease is
        not a refusal: that is an active local Turn, and queueing behind it
        is exactly what submission is for.
        """
        probe = self.session_store.acquire_run_lease(
            thread_id, holder=self.holder_label
        )
        if probe is not None:
            probe.close()
            return
        holder = self.session_store.run_holder(thread_id)
        if holder == self.holder_label:
            return
        raise ConflictError(
            f"session is being run by {holder or 'another process'}: {thread_id}",
            user_message=(
                "This Session is currently running a turn in "
                f"{holder or 'another DeepCode process'}. Wait for it to "
                "finish there, or continue in that window."
            ),
        )

    def _submitting_worker_id(self) -> str | None:
        coordinator = self._execution_coordinator
        worker = coordinator.worker if coordinator is not None else None
        return worker.id if worker is not None else None

    def _require_execution_coordinator(self) -> ExecutionCoordinator:
        coordinator = self._execution_coordinator
        if coordinator is None:
            raise RuntimeError("execution coordinator is not configured")
        return coordinator

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

    def list_for_thread(self, thread_id: str) -> tuple[Turn, ...]:
        """Return the durable Turn queue in ordinal order for client status UI."""

        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            turns = TurnRepository(connection).list_for_thread(thread_id)
        return tuple(turns)

    def list_for_goal(self, thread_id: str, goal_id: str) -> tuple[Turn, ...]:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            turns = TurnRepository(connection).list_for_goal(thread_id, goal_id)
        return tuple(turns)

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
                self._terminal_condition.wait(
                    min(remaining, _DURABLE_STATE_POLL_INTERVAL)
                )

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

    async def compact_live_context(self, thread_id: str) -> dict[str, Any]:
        """Summarize resident model context on demand (`/compact`).

        Canonical Session data is never rewritten — this is `/clear`'s gentler
        sibling: older turns collapse into a model-written handoff summary
        while recent user input survives verbatim.
        """
        if self.active_for_thread(thread_id) is not None:
            raise ConflictError("Compaction is unavailable while a Turn is active.")
        with self.database.read() as connection:
            thread = ThreadRepository(connection).get(thread_id)
        if thread is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        # Resolve the Thread's CURRENT selection exactly like a Turn does, so
        # compaction summarizes with the model the user selected — not with
        # whatever the resident runtime was built with before a switch.
        execution_profile = self.llm_configuration.resolve(
            thread.workspace_path,
            ExecutionSelection(
                connection_id=thread.connection_id,
                model_id=thread.model,
                reasoning_effort=thread.reasoning_effort,
            ),
        )
        return await self.session_runtimes.compact_live_history(
            thread_id,
            execution_profile=execution_profile,
        )

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
        if (
            snapshot.turn.status is TurnStatus.QUEUED
            and snapshot.turn.execution_owner_id is None
        ):
            try:
                interrupted = self._finish_unstarted(
                    turn_id,
                    status=TurnStatus.INTERRUPTED,
                    stop_reason="interrupted",
                    mark_cancel_requested=True,
                )
            except TurnWriteConflictError:
                # A coordinator claimed the Turn after the initial read.
                pass
            else:
                self.registry.interrupt(turn_id)
                self._remove_observer(turn_id)
                return True, interrupted
        requested = self._request_cancellation(turn_id)
        if requested.status.is_terminal:
            return False, requested
        accepted = self.registry.interrupt(turn_id)
        if not accepted and requested.execution_owner_id is not None:
            # The durable request is polled only by the claim-owning process.
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
                self._terminal_condition.wait(
                    min(remaining, _DURABLE_STATE_POLL_INTERVAL)
                )

    def _request_cancellation(self, turn_id: str) -> Turn:
        events: tuple[DomainEvent, ...] = ()
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            current = turns.get(turn_id)
            if current is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            if current.status.is_terminal or current.cancel_requested_at is not None:
                return current
            requested = replace(current, cancel_requested_at=utc_now())
            turns.update(requested)
            event = EventRepository(connection).append(
                thread_id=current.thread_id,
                turn_id=turn_id,
                type="turn.cancel_requested",
                payload={"turnId": turn_id},
            )
            events = (event,)
        self._publish(events)
        return requested

    async def _execute(
        self,
        turn_id: str,
        *,
        claim: ResourceClaim | None = None,
    ) -> None:
        projection: TurnEventProjector | None = None
        session_thread_id: str | None = None
        status = TurnStatus.FAILED
        stop_reason = "protocol_incomplete"
        error_code: str | None = "PROTOCOL_INCOMPLETE"
        error_message: str | None = "agent stream ended without task_complete"
        turn_usage: dict[str, int] = {}
        try:
            turn, workspace, execution_profile = self._mark_running(
                turn_id,
                claim=claim,
            )
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
                execution_security_profile=turn.execution_security_profile,
                permission_mode_override=turn.execution_permission_mode,
                approval_callback=approve,
            )
            self.session_runtimes.prepare_inputs(
                turn.thread_id,
                turn_id=turn.id,
            )
            skill_invocations: dict[str, SkillInvocation] = {}
            stored_user = False
            inputs_active = False
            turn_identity_metadata = {
                "executionClass": turn.execution_class.value,
                **({"goalId": turn.goal_id} if turn.goal_id is not None else {}),
            }
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
                                "executionSecurityProfile": (
                                    turn.execution_security_profile.to_dict()
                                    if turn.execution_security_profile is not None
                                    else None
                                ),
                                **turn_identity_metadata,
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
            self.session_runtimes.persist_kernel_history(
                turn.thread_id,
                getattr(session, "history", ()),
                extra_metadata={
                    "schemaVersion": 3,
                    "client": turn_client,
                    "turnId": turn.id,
                    "executionProfile": execution_profile.to_dict(),
                    "executionSecurityProfile": (
                        turn.execution_security_profile.to_dict()
                        if turn.execution_security_profile is not None
                        else None
                    ),
                    **turn_identity_metadata,
                    # Turn-level facts belong on every record this Turn
                    # persists. Leaving them only on the fallback append lost
                    # them entirely once the history path started writing the
                    # assistant record itself.
                    "skillInvocations": [
                        invocation.to_metadata()
                        for invocation in skill_invocations.values()
                    ],
                },
            )
            if projection.saw_terminal:
                if projection.final_text:
                    canonical = self.session_store.get_session(turn.thread_id)
                    already_stored = (
                        canonical is not None
                        and canonical.messages
                        and canonical.messages[-1].role == "assistant"
                        and canonical.messages[-1].content == projection.final_text
                    )
                    continuation_metadata = assistant_continuation_metadata(
                        getattr(session, "history", ())
                    )
                    stored_assistant = True
                    if already_stored:
                        self.session_runtimes.mark_persisted(turn.thread_id)
                    else:
                        stored_assistant = self.session_store.append_message(
                            turn.thread_id,
                            "assistant",
                            projection.final_text,
                            metadata={
                                "schemaVersion": 3,
                                "client": turn_client,
                                "turnId": turn.id,
                                "executionProfile": execution_profile.to_dict(),
                                "executionSecurityProfile": (
                                    turn.execution_security_profile.to_dict()
                                    if turn.execution_security_profile is not None
                                    else None
                                ),
                                **continuation_metadata,
                                **turn_identity_metadata,
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
            if session_thread_id is not None:
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
                claim=claim,
            )
        finally:
            self._remove_observer(turn_id)

    def _cancel_before_start(
        self,
        turn_id: str,
        *,
        claim: ResourceClaim | None = None,
    ) -> None:
        thread_id: str | None = None
        try:
            thread_id = self.read(turn_id).turn.thread_id
            self._finish_unstarted(
                turn_id,
                status=TurnStatus.INTERRUPTED,
                stop_reason="interrupted",
                claim=claim,
            )
        finally:
            if thread_id is not None:
                self.session_runtimes.release(
                    thread_id,
                    turn_id=turn_id,
                )
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
        claim: ResourceClaim | None = None,
        mark_cancel_requested: bool = False,
    ) -> Turn:
        return self._finish(
            turn_id,
            status=status,
            stop_reason=stop_reason,
            error_code=error_code,
            error_message=error_message,
            claim=claim,
            ensure_completion=True,
            mark_cancel_requested=mark_cancel_requested,
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

    def interrupt_unclaimed_queued_for_worker(self, worker_id: str) -> int:
        """Settle this closing worker's queued, never-started Turns."""

        with self.database.read() as connection:
            turn_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM turns "
                    "WHERE status = 'queued' "
                    "AND executor = ? "
                    "AND execution_owner_id IS NULL "
                    "AND home_worker_id = ? "
                    "ORDER BY enqueued_at, thread_id, ordinal, id",
                    (TurnExecutor.AGENT.value, worker_id),
                ).fetchall()
            ]
        for turn_id in turn_ids:
            self._finish_unstarted(
                turn_id,
                status=TurnStatus.INTERRUPTED,
                stop_reason="application_closed",
            )
        return len(turn_ids)

    def discard_session_runtime(self, session_id: str) -> None:
        """Release an idle Agent runtime whose canonical Session was deleted."""

        if session_id not in self.session_runtimes.live_session_ids:
            return
        self.registry.run_maintenance(lambda: self.session_runtimes.discard(session_id))

    def _mark_running(
        self,
        turn_id: str,
        *,
        claim: ResourceClaim | None = None,
    ) -> tuple[Turn, str, ExecutionProfile]:
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            turn = turns.get(turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            if turn.executor is not TurnExecutor.AGENT:
                raise TurnWriteConflictError(
                    f"Agent runtime cannot execute {turn.executor.value} Turn {turn_id}"
                )
            if turn.status is not TurnStatus.QUEUED:
                raise ConflictError(
                    "only a queued Turn can begin execution",
                    details={
                        "turnId": turn_id,
                        "status": turn.status.value,
                    },
                )
            if claim is not None:
                if claim.turn_id != turn_id:
                    raise TurnWriteConflictError(
                        f"claim does not belong to Turn: {turn_id}"
                    )
                if not RuntimeCoordinationRepository(connection).claim_is_current(
                    claim
                ):
                    raise TurnWriteConflictError(
                        f"Turn execution fence is stale: {turn_id}"
                    )
            elif turn.execution_owner_id is not None:
                raise TurnWriteConflictError(
                    f"owned Turn requires its execution claim: {turn_id}"
                )
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
        claim: ResourceClaim | None = None,
        ensure_completion: bool = False,
        recover_active: bool = False,
        terminal_event_type: str = "turn.completed",
        mark_cancel_requested: bool = False,
    ) -> Turn:
        if not status.is_terminal:
            raise ValueError("finish requires a terminal status")
        events: list[DomainEvent] = []
        schedule_next_id: str | None = None
        released_claim = False
        coordinator = self._execution_coordinator
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            current = turns.get(turn_id)
            if current is None:
                raise TurnNotFoundError(f"turn not found: {turn_id}")
            if current.status.is_terminal:
                if claim is not None:
                    if coordinator is None:
                        raise RuntimeError(
                            "cannot confirm a claim without an execution coordinator"
                        )
                    if RuntimeCoordinationRepository(connection).claim_is_current(
                        claim
                    ):
                        raise TurnWriteConflictError(
                            f"terminal Turn still holds its execution fence: {turn_id}"
                        )
                    # This covers a process surviving after the database commit
                    # but before the in-memory receipt was forgotten.
                    coordinator.confirm_released(claim)
                return current
            now = utc_now()
            if claim is not None:
                if claim.turn_id != turn_id:
                    raise TurnWriteConflictError(
                        f"claim does not belong to Turn: {turn_id}"
                    )
                if coordinator is None:
                    raise RuntimeError(
                        "cannot release a claim without an execution coordinator"
                    )
                release_at = max(
                    now,
                    *(lease.heartbeat_at for lease in claim.leases),
                )
                if not coordinator.release_in_transaction(
                    connection,
                    claim,
                    reason=stop_reason,
                    released_at=release_at,
                ):
                    raise TurnWriteConflictError(
                        f"Turn execution fence is stale: {turn_id}"
                    )
                released_claim = True
                now = max(now, release_at)
                current = turns.get(turn_id)
                if current is None:  # pragma: no cover - FK prevents deletion
                    raise TurnNotFoundError(f"turn not found: {turn_id}")
            elif current.execution_owner_id is not None:
                raise TurnWriteConflictError(
                    f"owned Turn requires its execution claim: {turn_id}"
                )

            event_repo = EventRepository(connection)
            items = ItemRepository(connection)
            if mark_cancel_requested and current.cancel_requested_at is None:
                current = replace(current, cancel_requested_at=now)
                events.append(
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=turn_id,
                        type="turn.cancel_requested",
                        payload={"turnId": turn_id},
                    )
                )
            if recover_active:
                approvals = ApprovalRepository(connection)
                for approval in approvals.pending_for_turn(turn_id):
                    cancelled = replace(
                        approval,
                        status=ApprovalStatus.CANCELLED,
                        decision={
                            "status": ApprovalStatus.CANCELLED.value,
                            "reason": stop_reason,
                        },
                        resolved_at=now,
                    )
                    approvals.update(cancelled)
                    events.append(
                        event_repo.append(
                            thread_id=current.thread_id,
                            turn_id=turn_id,
                            item_id=approval.item_id,
                            type="approval.resolved",
                            payload={"approval": approval_view(cancelled)},
                        )
                    )
                for item in items.list_active_for_turn(turn_id):
                    settled = replace(
                        item,
                        status=(
                            ItemStatus.DECLINED
                            if item.kind is ItemKind.APPROVAL_REQUEST
                            else ItemStatus.FAILED
                        ),
                        payload={
                            **item.payload,
                            "recoveredAfterWorkerCrash": True,
                        },
                        updated_at=now,
                    )
                    items.update(settled)
                    events.append(
                        event_repo.append(
                            thread_id=current.thread_id,
                            turn_id=turn_id,
                            item_id=settled.id,
                            type="item.updated",
                            payload={"item": item_view(settled)},
                        )
                    )

            if ensure_completion and not any(
                item.kind is ItemKind.COMPLETION
                for item in items.list_for_turn(turn_id)
            ):
                recorded_usage = aggregate_recorded_usage(
                    event_repo.list_for_turn(
                        current.thread_id,
                        turn_id,
                        event_type=TURN_USAGE_EVENT_TYPE,
                    )
                )
                completion = Item(
                    thread_id=current.thread_id,
                    turn_id=turn_id,
                    ordinal=items.next_ordinal(turn_id),
                    kind=ItemKind.COMPLETION,
                    status=(
                        ItemStatus.COMPLETED
                        if status is TurnStatus.COMPLETED
                        else ItemStatus.FAILED
                    ),
                    summary=f"Turn {status.value}: {stop_reason}",
                    payload={
                        "stopReason": stop_reason,
                        **({"usage": recorded_usage} if recorded_usage else {}),
                    },
                    created_at=now,
                    updated_at=now,
                )
                items.add(completion)
                events.append(
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=turn_id,
                        item_id=completion.id,
                        type="item.created",
                        payload={"item": item_view(completion)},
                    )
                )

            terminal = replace(
                current,
                status=status,
                stop_reason=stop_reason,
                error_code=error_code if status is TurnStatus.FAILED else None,
                error_message=error_message if status is TurnStatus.FAILED else None,
                home_worker_id=None,
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
            events.extend(
                (
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=turn_id,
                        type=terminal_event_type,
                        payload={"turn": turn_view(terminal)},
                    ),
                    event_repo.append(
                        thread_id=current.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(settled_thread)},
                    ),
                )
            )
        if released_claim:
            assert coordinator is not None
            assert claim is not None
            coordinator.confirm_released(claim)
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
            active_turns = [
                turn
                for turn in turns.list_active()
                if turn.executor is TurnExecutor.AGENT
                and turn.execution_owner_id is None
            ]
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
                    home_worker_id=None,
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


def _execution_class_for(source: TurnInputSource) -> ExecutionClass:
    if source is TurnInputSource.GOAL_CONTINUATION:
        return ExecutionClass.GOAL_CONTINUATION
    return ExecutionClass.INTERACTIVE


def _client_surface_value(value: object) -> str:
    try:
        return ClientSurface(value).value
    except (TypeError, ValueError):
        return ClientSurface.INTERNAL.value
