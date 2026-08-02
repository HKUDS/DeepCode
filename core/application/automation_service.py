"""Durable scheduling over canonical Goal and Turn lifecycles.

Automation is an application extension, not a second agent runtime. A
definition owns immutable instructions, each trigger owns one idempotent
occurrence, and each accepted run owns one real Thread Goal. Turns remain the
only execution unit; a Run settles from its Goal rather than from its first
Turn.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from core.application.automation_scheduler import AutomationSchedulerPort
from core.application.automation_permission_policy import (
    AutomationPermissionPolicy,
    WorkspaceAutomationPermissionPolicy,
)
from core.application.automation_schedule_policy import (
    AutomationOverlapDecision,
    AutomationSchedulePolicy,
    DefaultAutomationSchedulePolicy,
)

from core.application.errors import (
    AutomationBootstrapPendingError,
    AutomationNotFoundError,
    ConflictError,
    InvalidArgumentError,
    ProjectNotFoundError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
    TurnAlreadyRunningError,
    TurnNotFoundError,
)
from core.application.event_service import EventBroker
from core.application.goal_extension import (
    GoalContinuationRejected,
    GoalExtension,
    GoalIdentityRetiredError,
)
from core.application.project_service import ProjectService
from core.application.thread_service import ThreadService
from core.application.turn_service import (
    TurnAdmissionContext,
    TurnService,
    TurnTransactionContext,
    TurnTransactionContribution,
)
from core.application.views import automation_run_view, automation_view, thread_view
from core.domain.automation import (
    Automation,
    AutomationActivationStatus,
    AutomationOccurrence,
    AutomationRevision,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.common import new_id, utc_now
from core.domain.message_provenance import ClientSurface, TurnInputSource
from core.domain.project import TrustState
from core.domain.runtime_coordination import ExecutionClass
from core.domain.thread import Thread, ThreadMode
from core.domain.thread_goal import ThreadGoal, ThreadGoalStatus
from core.domain.turn import Turn, TurnStatus
from core.events import Event
from core.persistence.automation_repository import (
    AutomationOccurrenceRepository,
    AutomationRepository,
    AutomationRevisionRepository,
    AutomationRunRepository,
)
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.serde import dump_datetime
from core.persistence.thread_repository import ThreadRepository


MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 366 * 24 * 60 * 60
MAX_AUTOMATION_NAME = 120
MAX_AUTOMATION_PROMPT = 16_384
MAX_OCCURRENCE_KEY = 255
DEFAULT_RUN_STATE_POLL_SECONDS = 0.1
DEFAULT_AUTOMATION_PAGE_SIZE = 100
MAX_AUTOMATION_PAGE_SIZE = 500


@dataclass(frozen=True, slots=True)
class AutomationInventory:
    automations: tuple[Automation, ...]
    latest_runs: tuple[AutomationRun, ...]
    scheduler_active: bool
    has_more: bool = False
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class AutomationRunPage:
    runs: tuple[AutomationRun, ...]
    has_more: bool
    next_offset: int | None

    def __iter__(self) -> Iterator[AutomationRun]:
        """Keep existing service consumers iterable while exposing page metadata."""

        return iter(self.runs)

    def __len__(self) -> int:
        return len(self.runs)

    def __getitem__(
        self,
        index: int | slice,
    ) -> AutomationRun | tuple[AutomationRun, ...]:
        return self.runs[index]


@dataclass(frozen=True, slots=True)
class AutomationCreation:
    automation: Automation
    thread: Thread


@dataclass(frozen=True, slots=True)
class AutomationExecution:
    run: AutomationRun
    turn: Turn | None


class AutomationService:
    """Own Automation definitions and project them onto Goal/Turn runtime."""

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        projects: ProjectService,
        threads: ThreadService,
        turns: TurnService,
        goals: GoalExtension,
        *,
        schedule_policy: AutomationSchedulePolicy | None = None,
        permission_policy: AutomationPermissionPolicy | None = None,
    ) -> None:
        self.database = database
        self.broker = broker
        self.projects = projects
        self.threads = threads
        self.turns = turns
        self.goals = goals
        self.schedule_policy = schedule_policy or DefaultAutomationSchedulePolicy()
        self.permission_policy = (
            permission_policy or WorkspaceAutomationPermissionPolicy()
        )
        self._operation_lock = threading.RLock()
        self._scheduler: AutomationSchedulerPort | None = None

    @property
    def scheduler_active(self) -> bool:
        """Compatibility status projected through Automation inventory."""

        return self._scheduler is not None and self._scheduler.active

    def set_scheduler(self, scheduler: AutomationSchedulerPort) -> None:
        """Attach the composition-root-owned scheduler status/wake port."""

        self._scheduler = scheduler

    def ensure_goal_continuable(self, goal: ThreadGoal) -> None:
        """Reject orphan Turns after an Automation Run has settled."""

        with self.database.read() as connection:
            run = AutomationRunRepository(connection).get_for_goal(goal.id)
        if run is not None and run.turn_id is None:
            raise GoalContinuationRejected(
                "Automation is still provisioning its initial Turn; "
                "continue from the Automation after provisioning settles"
            )
        if run is not None and (
            run.status.is_terminal or goal.status is ThreadGoalStatus.COMPLETE
        ):
            raise GoalContinuationRejected(
                "A completed Automation Goal cannot be continued directly; "
                "run the Automation again to create a new Goal Run"
            )

    @staticmethod
    def ensure_turn_admitted(context: TurnAdmissionContext) -> None:
        """Protect a pending initial Turn through an owner-neutral reservation.

        The guard executes inside TurnService's write transaction. It only
        understands durable Run ownership and never inspects task text.
        """

        runs = AutomationRunRepository(context.connection)
        owned = (
            runs.get_for_goal(context.goal_id) if context.goal_id is not None else None
        )
        if owned is not None and owned.status.is_terminal:
            raise ConflictError("A settled Automation Goal cannot accept another Turn")

        open_run = runs.open_for_thread(context.thread.id)
        if open_run is None:
            return
        if context.goal_id != open_run.goal_id:
            raise ConflictError(
                "The Automation Goal Thread is reserved by its open Run"
            )
        if open_run.turn_id is not None:
            terminal_turn_ids = tuple(
                turn.id
                for turn in TurnRepository(context.connection).list_for_goal(
                    open_run.thread_id,
                    open_run.goal_id,
                )
                if turn.status.is_terminal
            )
            if any(
                turn_id not in context.goal_turn_settlement_ids
                for turn_id in terminal_turn_ids
            ):
                raise ConflictError("Automation Goal Turn settlement is incomplete")
            return
        if (
            context.reservation_id == open_run.id
            and context.input_source is TurnInputSource.AUTOMATION
        ):
            return
        raise ConflictError(
            "Automation is reserving the Goal Thread for its initial Turn"
        )

    def list(
        self,
        project_id: str | None = None,
        *,
        limit: int = DEFAULT_AUTOMATION_PAGE_SIZE,
        offset: int = 0,
    ) -> AutomationInventory:
        _validate_page(limit=limit, offset=offset)
        if project_id is not None:
            self.projects.read(project_id)
        self.reconcile_runs()
        with self.database.read() as connection:
            candidates = AutomationRepository(connection).list(
                project_id=project_id,
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(candidates) > limit
            definitions = candidates[:limit]
            run_repo = AutomationRunRepository(connection)
            latest = tuple(
                run
                for definition in definitions
                if (run := run_repo.latest_for_automation(definition.id)) is not None
            )
        return AutomationInventory(
            automations=tuple(definitions),
            latest_runs=latest,
            scheduler_active=self.scheduler_active,
            has_more=has_more,
            next_offset=(offset + len(definitions) if has_more else None),
        )

    def read(self, automation_id: str) -> Automation:
        return self._read_definition(automation_id, include_retired=False)

    def create(
        self,
        *,
        project_id: str,
        name: str,
        prompt: str,
        schedule_kind: AutomationScheduleKind,
        interval_seconds: int | None = None,
        enabled: bool = True,
    ) -> AutomationCreation:
        clean_name = _clean_name(name)
        clean_prompt = _clean_prompt(prompt)
        if schedule_kind is AutomationScheduleKind.MANUAL and not enabled:
            raise InvalidArgumentError(
                "manual automations are always enabled; only interval schedules "
                "can be paused"
            )
        interval = _validated_interval(schedule_kind, interval_seconds)
        now = utc_now()
        status = AutomationStatus.ENABLED if enabled else AutomationStatus.PAUSED
        next_run_at = (
            self.schedule_policy.initial_interval_deadline(
                activated_at=now,
                interval_seconds=interval,
            )
            if schedule_kind is AutomationScheduleKind.INTERVAL
            and status is AutomationStatus.ENABLED
            and interval is not None
            else None
        )
        automation_id = new_id("auto")
        thread_id = new_id("thr")
        revision = AutomationRevision(
            automation_id=automation_id,
            ordinal=1,
            instruction=clean_prompt,
            created_at=now,
        )

        with self.database.transaction() as connection:
            project = ProjectRepository(connection).get(project_id)
            if project is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            if project.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must be trusted before an automation can be created"
                )
            thread = Thread(
                id=thread_id,
                project_id=project.id,
                workspace_path=project.canonical_path,
                title=clean_name,
                mode=ThreadMode.GOAL,
                created_at=now,
                updated_at=now,
            )
            automation = Automation(
                id=automation_id,
                project_id=project.id,
                thread_id=thread.id,
                name=clean_name,
                current_revision_id=revision.id,
                prompt=revision.instruction,
                schedule_kind=schedule_kind,
                status=status,
                interval_seconds=interval,
                next_run_at=next_run_at,
                created_at=now,
                updated_at=now,
            )
            ThreadRepository(connection).add(thread)
            AutomationRevisionRepository(connection).add(revision)
            AutomationRepository(connection).add(automation)
            thread_event = EventRepository(connection).append(
                thread_id=thread.id,
                type="thread.created",
                payload={"thread": thread_view(thread)},
            )
            automation_event = EventRepository(connection).append(
                thread_id=thread.id,
                type="automation.updated",
                payload={"automation": automation_view(automation)},
            )

        try:
            self.threads.materialize_session(thread.id)
        except ThreadNotFoundError as exc:
            with self.database.read() as connection:
                durable_thread = ThreadRepository(connection).get(thread.id)
                durable_automation = AutomationRepository(connection).get(
                    automation.id,
                    include_retired=True,
                )
            if durable_thread is None or durable_automation is None:
                raise AutomationNotFoundError(
                    "automation creation lost ownership because its project was removed"
                ) from exc
            raise AutomationBootstrapPendingError(
                automation.id,
                thread.id,
            ) from exc
        except Exception as exc:
            raise AutomationBootstrapPendingError(
                automation.id,
                thread.id,
            ) from exc

        self.broker.publish(thread_event)
        self.broker.publish(automation_event)
        self._wake_scheduler()
        return AutomationCreation(automation, thread)

    def update(
        self,
        automation_id: str,
        *,
        name: str | None = None,
        prompt: str | None = None,
        status: AutomationActivationStatus | None = None,
        schedule_kind: AutomationScheduleKind | None = None,
        interval_seconds: int | None = None,
    ) -> Automation:
        next_activation_status = _validated_activation_status(status)
        with self._operation_lock:
            with self.database.transaction() as connection:
                definitions = AutomationRepository(connection)
                revisions = AutomationRevisionRepository(connection)
                current = definitions.get(automation_id, include_retired=True)
                if current is None:
                    raise AutomationNotFoundError(
                        f"automation not found: {automation_id}"
                    )
                if current.status is AutomationStatus.RETIRED:
                    raise ConflictError("a retired automation cannot be updated")
                next_kind = schedule_kind or current.schedule_kind
                if (
                    next_kind is AutomationScheduleKind.MANUAL
                    and next_activation_status is AutomationStatus.PAUSED
                ):
                    raise InvalidArgumentError(
                        "manual automations are always enabled; only interval "
                        "schedules can be paused"
                    )
                next_status = (
                    AutomationStatus.ENABLED
                    if next_kind is AutomationScheduleKind.MANUAL
                    else next_activation_status or current.status
                )
                next_interval = _validated_interval(
                    next_kind,
                    (
                        interval_seconds
                        if interval_seconds is not None
                        else current.interval_seconds
                    ),
                )
                if next_status is AutomationStatus.ENABLED:
                    project = ProjectRepository(connection).get(current.project_id)
                    if project is None:
                        raise ConflictError("automation project is missing")
                    if project.trust_state is not TrustState.TRUSTED:
                        raise ProjectNotTrustedError(
                            "project must be trusted before an automation is enabled"
                        )
                now = utc_now()
                schedule_changed = (
                    next_kind is not current.schedule_kind
                    or next_interval != current.interval_seconds
                    or (
                        current.status is AutomationStatus.PAUSED
                        and next_status is AutomationStatus.ENABLED
                    )
                )
                if next_kind is AutomationScheduleKind.MANUAL:
                    next_run_at = None
                elif next_status is AutomationStatus.PAUSED:
                    next_run_at = None
                elif schedule_changed or current.next_run_at is None:
                    assert next_interval is not None
                    next_run_at = self.schedule_policy.initial_interval_deadline(
                        activated_at=now,
                        interval_seconds=next_interval,
                    )
                else:
                    next_run_at = current.next_run_at

                next_prompt = (
                    _clean_prompt(prompt) if prompt is not None else current.prompt
                )
                current_revision_id = current.current_revision_id
                if next_prompt != current.prompt:
                    revision = AutomationRevision(
                        automation_id=current.id,
                        ordinal=revisions.next_ordinal(current.id),
                        instruction=next_prompt,
                        created_at=now,
                    )
                    revisions.add(revision)
                    current_revision_id = revision.id

                updated = replace(
                    current,
                    name=_clean_name(name) if name is not None else current.name,
                    current_revision_id=current_revision_id,
                    prompt=next_prompt,
                    status=next_status,
                    schedule_kind=next_kind,
                    interval_seconds=next_interval,
                    next_run_at=next_run_at,
                    updated_at=now,
                )
                if not definitions.update(
                    updated,
                    expected_current_revision_id=current.current_revision_id,
                    expected_updated_at=current.updated_at,
                ):
                    raise ConflictError(
                        "automation changed concurrently; refresh and retry"
                    )
                event = EventRepository(connection).append(
                    thread_id=updated.thread_id,
                    type="automation.updated",
                    payload={"automation": automation_view(updated)},
                )
            self.broker.publish(event)
            self._wake_scheduler()
            return updated

    def remove(self, automation_id: str) -> bool:
        """Retire a definition without deleting its revisions or run history."""

        with self._operation_lock:
            with self.database.transaction() as connection:
                definitions = AutomationRepository(connection)
                current = definitions.get(automation_id, include_retired=True)
                if current is None:
                    raise AutomationNotFoundError(
                        f"automation not found: {automation_id}"
                    )
                if current.status is AutomationStatus.RETIRED:
                    return False
                open_run = AutomationRunRepository(connection).open_for_automation(
                    automation_id
                )
                if open_run is not None:
                    raise ConflictError(
                        "cannot retire an automation while its Goal is open"
                    )
                retired = replace(
                    current,
                    status=AutomationStatus.RETIRED,
                    next_run_at=None,
                    updated_at=utc_now(),
                )
                if not definitions.update(
                    retired,
                    expected_current_revision_id=current.current_revision_id,
                    expected_updated_at=current.updated_at,
                ):
                    raise ConflictError(
                        "automation changed concurrently; refresh and retry"
                    )
                event = EventRepository(connection).append(
                    thread_id=retired.thread_id,
                    type="automation.updated",
                    payload={
                        "automation": automation_view(retired),
                        "automationId": retired.id,
                        "removed": True,
                    },
                )
            self.broker.publish(event)
            self._wake_scheduler()
            return True

    def list_runs(
        self,
        automation_id: str,
        *,
        limit: int = DEFAULT_AUTOMATION_PAGE_SIZE,
        offset: int = 0,
    ) -> AutomationRunPage:
        _validate_page(limit=limit, offset=offset)
        self._read_definition(automation_id, include_retired=True)
        self.reconcile_runs()
        with self.database.read() as connection:
            candidates = AutomationRunRepository(connection).list_for_automation(
                automation_id,
                limit=limit + 1,
                offset=offset,
            )
        has_more = len(candidates) > limit
        runs = tuple(candidates[:limit])
        return AutomationRunPage(
            runs=runs,
            has_more=has_more,
            next_offset=(offset + len(runs) if has_more else None),
        )

    def wait_until_terminal(
        self,
        run_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = DEFAULT_RUN_STATE_POLL_SECONDS,
    ) -> AutomationRun | None:
        """Wait for one durable Run without imposing a product time budget.

        Polling is required because another process may own the Turn and update
        the shared database. ``timeout=None`` intentionally means that the
        caller owns the lifetime decision (for example, Ctrl-C in a CLI).
        """

        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            with self._operation_lock:
                with self.database.read() as connection:
                    current = AutomationRunRepository(connection).get(run_id)
                if current is None:
                    raise AutomationNotFoundError(f"automation run not found: {run_id}")
                if current.status.is_terminal:
                    return current
                current = (
                    self._provision_run(current).run
                    if current.goal_id is not None and current.turn_id is None
                    else self._reconcile_run(current)
                )
                if current.status.is_terminal:
                    return current

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(poll_interval, remaining))
            else:
                time.sleep(poll_interval)

    def run_now(
        self,
        automation_id: str,
        *,
        request_id: str | None = None,
    ) -> AutomationExecution:
        key = _manual_occurrence_key(request_id)
        execution = self._trigger(
            automation_id,
            trigger=AutomationTrigger.MANUAL,
            scheduled_for=utc_now(),
            occurrence_key=key,
        )
        assert execution is not None
        return execution

    def run_due(self, now: datetime | None = None) -> tuple[AutomationRun, ...]:
        current_time = now or utc_now()
        with self._operation_lock:
            self._reconcile_runs_locked()
            with self.database.read() as connection:
                due = AutomationRepository(connection).list_due(current_time)
            executions: list[AutomationExecution] = []
            for definition in due:
                scheduled_for = definition.next_run_at or current_time
                execution = self._trigger(
                    definition.id,
                    trigger=AutomationTrigger.SCHEDULED,
                    scheduled_for=scheduled_for,
                    occurrence_key=_scheduled_occurrence_key(scheduled_for),
                    now=current_time,
                )
                if execution is not None:
                    executions.append(execution)
        return tuple(execution.run for execution in executions)

    def reconcile_runs(self) -> int:
        with self._operation_lock:
            return self._reconcile_runs_locked()

    def next_due(self) -> datetime | None:
        """Return the next persisted schedule deadline for scheduler timing."""

        with self.database.read() as connection:
            return AutomationRepository(connection).next_due_at()

    def on_turn_settled(self, turn: Turn) -> None:
        """Settle an Automation Run only after GoalExtension has accounted Turn."""

        if turn.goal_id is None:
            return
        with self._operation_lock:
            with self.database.read() as connection:
                run = AutomationRunRepository(connection).get_for_goal(turn.goal_id)
            if run is not None and not run.status.is_terminal:
                self._reconcile_run(run)

    def _reconcile_runs_locked(self) -> int:
        with self.database.read() as connection:
            active = AutomationRunRepository(connection).list_active()
        changed = 0
        for run in active:
            before = run
            if run.goal_id is not None and run.turn_id is None:
                after = self._provision_run(run).run
            else:
                after = self._reconcile_run(run)
            if after != before:
                changed += 1
        return changed

    def _trigger(
        self,
        automation_id: str,
        *,
        trigger: AutomationTrigger,
        scheduled_for: datetime,
        occurrence_key: str,
        now: datetime | None = None,
    ) -> AutomationExecution | None:
        with self._operation_lock:
            current_time = now or utc_now()
            with self.database.transaction() as connection:
                definitions = AutomationRepository(connection)
                definition = definitions.get(
                    automation_id,
                    include_retired=True,
                )
                if definition is None:
                    raise AutomationNotFoundError(
                        f"automation not found: {automation_id}"
                    )
                if definition.status is AutomationStatus.RETIRED:
                    raise AutomationNotFoundError(
                        f"automation not found: {automation_id}"
                    )
                if trigger is AutomationTrigger.SCHEDULED and (
                    definition.status is not AutomationStatus.ENABLED
                    or definition.schedule_kind is not AutomationScheduleKind.INTERVAL
                ):
                    raise InvalidArgumentError("automation is not scheduled")

                occurrences = AutomationOccurrenceRepository(connection)
                existing_occurrence = occurrences.get_by_key(
                    definition.id,
                    trigger,
                    occurrence_key,
                )
                if existing_occurrence is not None:
                    existing_run = AutomationRunRepository(
                        connection
                    ).get_for_occurrence(existing_occurrence.id)
                    if existing_run is None:
                        raise ConflictError(
                            "automation occurrence exists without its Run"
                        )
                    duplicate = existing_run
                    event = None
                else:
                    if (
                        trigger is AutomationTrigger.SCHEDULED
                        and definition.next_run_at != scheduled_for
                    ):
                        return None
                    next_run_at = definition.next_run_at
                    if (
                        trigger is AutomationTrigger.SCHEDULED
                        and definition.interval_seconds is not None
                    ):
                        next_run_at = self.schedule_policy.advance_due_interval(
                            scheduled_for=scheduled_for,
                            observed_at=current_time,
                            interval_seconds=definition.interval_seconds,
                        ).next_run_at
                    touched = replace(
                        definition,
                        next_run_at=next_run_at,
                        last_run_at=current_time,
                        updated_at=current_time,
                    )
                    if trigger is AutomationTrigger.SCHEDULED:
                        if not definitions.claim_due(
                            touched,
                            expected_next_run_at=scheduled_for,
                        ):
                            return None
                    else:
                        if not definitions.update(
                            touched,
                            expected_current_revision_id=definition.current_revision_id,
                            expected_updated_at=definition.updated_at,
                        ):
                            raise ConflictError(
                                "automation changed concurrently; retry the Run"
                            )

                    occurrence = AutomationOccurrence(
                        automation_id=touched.id,
                        kind=trigger,
                        occurrence_key=occurrence_key,
                        nominal_at=scheduled_for,
                        observed_at=current_time,
                    )
                    occurrences.add(occurrence)
                    runs = AutomationRunRepository(connection)
                    open_run = runs.open_for_automation(touched.id)
                    project = ProjectRepository(connection).get(touched.project_id)
                    if project is None:
                        status = AutomationRunStatus.FAILED
                        detail = "Automation project is missing"
                    elif project.trust_state is not TrustState.TRUSTED:
                        status = AutomationRunStatus.SKIPPED
                        detail = "Project is not trusted"
                    elif (
                        self.schedule_policy.decide_overlap(
                            has_open_run=open_run is not None
                        )
                        is AutomationOverlapDecision.SKIP
                    ):
                        assert open_run is not None
                        status = AutomationRunStatus.SKIPPED
                        detail = f"Previous Goal Run {open_run.id} is still active/open"
                    else:
                        status = AutomationRunStatus.QUEUED
                        detail = ""
                    terminal = status.is_terminal
                    run = AutomationRun(
                        automation_id=touched.id,
                        revision_id=touched.current_revision_id,
                        occurrence_id=occurrence.id,
                        goal_id=(new_id("goal") if not terminal else None),
                        thread_id=touched.thread_id,
                        trigger=trigger,
                        status=status,
                        scheduled_for=scheduled_for,
                        detail=detail,
                        created_at=current_time,
                        updated_at=current_time,
                        completed_at=current_time if terminal else None,
                    )
                    runs.add(run)
                    event = EventRepository(connection).append(
                        thread_id=touched.thread_id,
                        type="automation.updated",
                        payload={
                            "automation": automation_view(touched),
                            "run": automation_run_view(run),
                        },
                    )
                    duplicate = None
            if duplicate is not None:
                return self._execution_for_run(duplicate, provision=True)
            assert event is not None
            self.broker.publish(event)
            if run.status.is_terminal:
                return AutomationExecution(run, None)
            return self._provision_run(run)

    def _provision_run(self, run: AutomationRun) -> AutomationExecution:
        """Converge a durable queued Run onto one Goal and one initial Turn."""

        with self.database.read() as connection:
            runs = AutomationRunRepository(connection)
            current = runs.get(run.id)
            revision = AutomationRevisionRepository(connection).get(run.revision_id)
            thread = ThreadRepository(connection).get(run.thread_id)
        if current is None:
            raise AutomationNotFoundError(f"automation run not found: {run.id}")
        if current.status.is_terminal:
            return self._execution_for_run(current)
        if current.goal_id is None:
            return AutomationExecution(
                self._transition_run(
                    current,
                    status=AutomationRunStatus.FAILED,
                    detail="Automation Run is missing its Goal identity",
                    terminal=True,
                ),
                None,
            )
        if revision is None:
            return AutomationExecution(
                self._transition_run(
                    current,
                    status=AutomationRunStatus.FAILED,
                    detail="Automation instruction revision is missing",
                    terminal=True,
                ),
                None,
            )
        if thread is None:
            return AutomationExecution(
                self._transition_run(
                    current,
                    status=AutomationRunStatus.FAILED,
                    detail="Automation Goal Thread is missing",
                    terminal=True,
                ),
                None,
            )

        # A durable event may reach another live process before the creating
        # process materializes the canonical Session. Use the narrow bootstrap
        # repair here; a full Thread read would also reconcile transcript
        # markers and could create unrelated synthetic Turns.
        try:
            self.threads.materialize_session(current.thread_id)
            existing_goal = self.goals.read(current.thread_id)
        except Exception as exc:  # noqa: BLE001 - settle the durable Run boundary
            return self._settle_provisioning_boundary_failure(current, exc)
        replace_goal_id: str | None = None
        if existing_goal is not None and existing_goal.id != current.goal_id:
            if (
                existing_goal.status is not ThreadGoalStatus.COMPLETE
                or self.turns.active_for_thread(current.thread_id) is not None
            ):
                blocked = self._transition_run(
                    current,
                    status=AutomationRunStatus.BLOCKED,
                    detail=(
                        "Automation Goal Thread currently owns a different "
                        f"Goal ({existing_goal.id})"
                    ),
                )
                return AutomationExecution(blocked, None)
            replace_goal_id = existing_goal.id

        try:
            goal = (
                self.goals.provision_replacing_completed(
                    current.thread_id,
                    expected_current_goal_id=replace_goal_id,
                    goal_id=current.goal_id,
                    objective=revision.instruction,
                )
                if replace_goal_id is not None
                else self.goals.provision(
                    current.thread_id,
                    goal_id=current.goal_id,
                    objective=revision.instruction,
                )
            )
        except GoalIdentityRetiredError as exc:
            interrupted = self._transition_run(
                current,
                status=AutomationRunStatus.INTERRUPTED,
                detail=str(exc),
                terminal=True,
            )
            return AutomationExecution(interrupted, None)
        except ConflictError as exc:
            blocked = self._transition_run(
                current,
                status=AutomationRunStatus.BLOCKED,
                detail=str(exc),
            )
            return AutomationExecution(blocked, None)

        if goal.status is not ThreadGoalStatus.ACTIVE:
            reconciled = self._reconcile_run(current)
            return self._execution_for_run(reconciled)
        if current.turn_id is not None:
            reconciled = self._reconcile_run(current)
            return self._execution_for_run(reconciled)

        def attach(
            context: TurnTransactionContext,
        ) -> TurnTransactionContribution[AutomationRun]:
            if context.turn.goal_id != current.goal_id:
                raise ConflictError(
                    "Automation Turn was not attributed to the expected Goal"
                )
            run_repo = AutomationRunRepository(context.connection)
            latest = run_repo.get(current.id)
            if latest is None:
                raise AutomationNotFoundError(f"automation run not found: {current.id}")
            if latest.status.is_terminal:
                raise ConflictError("Automation Run settled before Turn attachment")
            if latest.turn_id is not None:
                raise ConflictError(
                    f"Automation Run already owns Turn {latest.turn_id}"
                )
            attached = replace(
                latest,
                turn_id=context.turn.id,
                status=AutomationRunStatus.QUEUED,
                detail="",
                updated_at=_not_before(latest.updated_at),
                completed_at=None,
            )
            if not run_repo.update(
                attached,
                expected_status=latest.status,
                expected_updated_at=latest.updated_at,
            ):
                raise ConflictError("Automation Run changed during Turn attachment")
            attached = replace(attached, version=latest.version + 1)
            definition = AutomationRepository(context.connection).get(
                attached.automation_id,
                include_retired=True,
            )
            event = EventRepository(context.connection).append(
                thread_id=attached.thread_id,
                turn_id=context.turn.id,
                type="automation.updated",
                payload={
                    **(
                        {"automation": automation_view(definition)}
                        if definition is not None
                        else {}
                    ),
                    "run": automation_run_view(attached),
                },
            )
            return TurnTransactionContribution(
                value=attached,
                events=(event,),
            )

        try:
            submitted = self.turns.start_with_participant(
                current.thread_id,
                prompt=revision.instruction,
                participant=attach,
                reservation_id=current.id,
                event_observer=lambda event: self._observe_run(current.id, event),
                client_surface=ClientSurface.AUTOMATION,
                input_source=TurnInputSource.AUTOMATION,
                expected_goal_id=current.goal_id,
                execution_class=(
                    ExecutionClass.SCHEDULED_AUTOMATION
                    if current.trigger is AutomationTrigger.SCHEDULED
                    else ExecutionClass.MANUAL_AUTOMATION
                ),
                execution_permission_mode=self.permission_policy.resolve(
                    thread.workspace_path
                ),
            )
        except TurnAlreadyRunningError as exc:
            actual_turn_id = exc.details.get("actualTurnId")
            if isinstance(actual_turn_id, str):
                try:
                    active = self.turns.read(actual_turn_id).turn
                except TurnNotFoundError:
                    active = None
                if active is not None and active.goal_id == current.goal_id:
                    with self.database.read() as connection:
                        latest = AutomationRunRepository(connection).get(current.id)
                    if (
                        latest is not None
                        and latest.turn_id is not None
                        and latest.turn_id == active.id
                    ):
                        reconciled = self._reconcile_run(latest)
                        return self._execution_for_run(reconciled)
            blocked = self._transition_run(
                current,
                status=AutomationRunStatus.BLOCKED,
                detail="Goal Thread is executing unrelated work",
            )
            return AutomationExecution(blocked, None)
        except Exception as exc:  # noqa: BLE001 - durable state is reconciled below
            with self.database.read() as connection:
                committed = AutomationRunRepository(connection).get(current.id)
            if committed is not None and committed.turn_id is not None:
                reconciled = self._reconcile_run(committed)
                return self._execution_for_run(reconciled)
            blocked = self._transition_run(
                committed or current,
                status=AutomationRunStatus.BLOCKED,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return AutomationExecution(blocked, None)

        reconciled = self._reconcile_run(submitted.participant_result)
        return AutomationExecution(reconciled, submitted.snapshot.turn)

    def _settle_provisioning_boundary_failure(
        self,
        run: AutomationRun,
        error: Exception,
    ) -> AutomationExecution:
        """Never strand a Run when Session/Goal bootstrap cannot be entered."""

        with self.database.read() as connection:
            current = AutomationRunRepository(connection).get(run.id)
        if current is None:
            raise AutomationNotFoundError(
                f"automation run no longer exists: {run.id}"
            ) from error
        if current.status.is_terminal:
            return self._execution_for_run(current)
        if current.turn_id is not None:
            reconciled = self._reconcile_run(current)
            return self._execution_for_run(reconciled)
        if isinstance(error, ThreadNotFoundError):
            failed = self._transition_run(
                current,
                status=AutomationRunStatus.FAILED,
                detail="Automation Goal Thread became unavailable during provisioning",
                terminal=True,
            )
            return AutomationExecution(failed, None)
        blocked = self._transition_run(
            current,
            status=AutomationRunStatus.BLOCKED,
            detail=(
                "Canonical Session bootstrap is unavailable: "
                f"{type(error).__name__}: {error}"
            ),
        )
        return AutomationExecution(blocked, None)

    def _execution_for_run(
        self,
        run: AutomationRun,
        *,
        provision: bool = False,
    ) -> AutomationExecution:
        if provision and not run.status.is_terminal and run.turn_id is None:
            return self._provision_run(run)
        turn: Turn | None = None
        if run.turn_id is not None:
            try:
                turn = self.turns.read(run.turn_id).turn
            except TurnNotFoundError:
                turn = None
        return AutomationExecution(run, turn)

    def _reconcile_run(self, run: AutomationRun) -> AutomationRun:
        with self.database.read() as connection:
            latest = AutomationRunRepository(connection).get(run.id)
        if latest is None:
            raise AutomationNotFoundError(f"automation run not found: {run.id}")
        if latest.status.is_terminal:
            return latest
        desired = self._desired_run_state(latest)
        # Reconciliation may inspect a stable non-terminal state repeatedly.
        # ``updated_at`` is an observation timestamp, not a state transition;
        # do not turn it into a write/event heartbeat when every durable
        # lifecycle field is unchanged.
        if replace(desired, updated_at=latest.updated_at) == latest:
            return latest
        return self._persist_run_state(latest, desired)

    def _desired_run_state(self, run: AutomationRun) -> AutomationRun:
        now = _not_before(run.updated_at)
        if run.goal_id is None:
            if run.turn_id is None:
                return replace(
                    run,
                    status=AutomationRunStatus.FAILED,
                    detail="Legacy Automation submission was interrupted",
                    updated_at=now,
                    completed_at=now,
                )
            try:
                turn = self.turns.read(run.turn_id).turn
            except TurnNotFoundError:
                return replace(
                    run,
                    status=AutomationRunStatus.FAILED,
                    detail="Automation Turn record is missing",
                    updated_at=now,
                    completed_at=now,
                )
            return _run_from_turn(run, turn)

        self.goals.reconcile_turn_settlements(
            run.thread_id,
            expected_goal_id=run.goal_id,
            continue_from_latest_completion=True,
        )
        active = self.turns.active_for_thread(run.thread_id)
        if active is not None and active.goal_id == run.goal_id:
            if run.turn_id is None:
                return replace(
                    run,
                    status=AutomationRunStatus.BLOCKED,
                    detail=(
                        f"Unowned Turn {active.id} is active before the "
                        "Automation initial Turn was committed"
                    ),
                    updated_at=now,
                    completed_at=None,
                )
            # Clearing or replacing a Goal must not release the Run's
            # one-open-run guard while its attributed Turn is still executing.
            return _run_from_turn(run, active, terminal_from_turn=False)

        goal = self.goals.read(run.thread_id)
        if goal is None:
            if run.turn_id is None:
                return run
            return replace(
                run,
                status=AutomationRunStatus.INTERRUPTED,
                detail="Automation Goal was cleared",
                updated_at=now,
                completed_at=now,
            )
        if goal.id != run.goal_id:
            return replace(
                run,
                status=AutomationRunStatus.INTERRUPTED,
                detail=f"Automation Goal was replaced by {goal.id}",
                updated_at=now,
                completed_at=now,
            )

        if active is not None:
            return replace(
                run,
                status=AutomationRunStatus.BLOCKED,
                detail=f"Unrelated Turn {active.id} is active in the Goal Thread",
                updated_at=now,
                completed_at=None,
            )

        goal_turns = self.turns.list_for_goal(run.thread_id, run.goal_id)
        terminal_turn_ids = tuple(
            turn.id for turn in goal_turns if turn.status.is_terminal
        )
        if terminal_turn_ids and not self.goals.are_turns_accounted(
            run.thread_id,
            goal_id=run.goal_id,
            turn_ids=terminal_turn_ids,
        ):
            return replace(
                run,
                status=AutomationRunStatus.WAITING,
                detail="Awaiting durable Goal settlement",
                updated_at=now,
                completed_at=None,
            )

        if run.turn_id is None:
            if goal_turns:
                return replace(
                    run,
                    status=AutomationRunStatus.INTERRUPTED,
                    detail=(
                        "Automation initial Turn was never committed; "
                        f"Goal contains unowned Turn {goal_turns[-1].id}"
                    ),
                    updated_at=now,
                    completed_at=now,
                )
            if goal.status is not ThreadGoalStatus.ACTIVE:
                return replace(
                    run,
                    status=AutomationRunStatus.BLOCKED,
                    detail=(
                        "Automation initial Turn was never committed; "
                        f"Goal is {goal.status.value.replace('_', ' ')}"
                    ),
                    updated_at=now,
                    completed_at=None,
                )
            return replace(
                run,
                status=AutomationRunStatus.QUEUED,
                detail="",
                updated_at=now,
                completed_at=None,
            )

        latest_goal_turn: Turn
        try:
            initial = self.turns.read(run.turn_id).turn
        except TurnNotFoundError:
            return replace(
                run,
                status=AutomationRunStatus.BLOCKED,
                detail="Automation initial Turn is missing",
                updated_at=now,
                completed_at=None,
            )
        else:
            latest_goal_turn = goal_turns[-1] if goal_turns else initial

        outcome = self.goals.read_outcome(run.thread_id)
        if goal.status is ThreadGoalStatus.COMPLETE:
            return replace(
                run,
                status=AutomationRunStatus.COMPLETED,
                detail=(outcome.reason if outcome is not None else "Goal completed")[
                    :2_000
                ],
                updated_at=now,
                completed_at=now,
            )
        if goal.status in {
            ThreadGoalStatus.BLOCKED,
            ThreadGoalStatus.BUDGET_LIMITED,
            ThreadGoalStatus.PAUSED,
        }:
            detail = (
                outcome.reason
                if outcome is not None
                else f"Goal is {goal.status.value.replace('_', ' ')}"
            )
            return replace(
                run,
                status=AutomationRunStatus.BLOCKED,
                detail=detail[:2_000],
                updated_at=now,
                completed_at=None,
            )

        if latest_goal_turn.status is TurnStatus.INTERRUPTED:
            return replace(
                run,
                status=AutomationRunStatus.BLOCKED,
                detail=(
                    latest_goal_turn.stop_reason
                    or "Automation Turn was interrupted; continue its Goal explicitly"
                ),
                updated_at=now,
                completed_at=None,
            )
        if latest_goal_turn.status is TurnStatus.FAILED:
            return replace(
                run,
                status=AutomationRunStatus.BLOCKED,
                detail=(
                    latest_goal_turn.error_message
                    or latest_goal_turn.stop_reason
                    or "Automation Turn failed; continue its Goal explicitly"
                )[:2_000],
                updated_at=now,
                completed_at=None,
            )
        return replace(
            run,
            status=AutomationRunStatus.BLOCKED,
            detail="Goal is active but idle; continue it from the Goal Thread",
            updated_at=now,
            completed_at=None,
        )

    def _persist_run_state(
        self,
        previous: AutomationRun,
        desired: AutomationRun,
    ) -> AutomationRun:
        with self.database.transaction() as connection:
            runs = AutomationRunRepository(connection)
            current = runs.get(previous.id)
            if current is None:
                raise AutomationNotFoundError(
                    f"automation run not found: {previous.id}"
                )
            if current.status.is_terminal:
                return current
            if current.version != previous.version:
                return current
            if replace(desired, updated_at=current.updated_at) == current:
                return current
            if not runs.update(
                desired,
                expected_status=current.status,
                expected_updated_at=current.updated_at,
            ):
                return runs.get(previous.id) or current
            desired = replace(desired, version=current.version + 1)
            definition = AutomationRepository(connection).get(
                desired.automation_id,
                include_retired=True,
            )
            event = EventRepository(connection).append(
                thread_id=desired.thread_id,
                turn_id=desired.turn_id,
                type="automation.updated",
                payload={
                    **(
                        {"automation": automation_view(definition)}
                        if definition is not None
                        else {}
                    ),
                    "run": automation_run_view(desired),
                },
            )
        self.broker.publish(event)
        return desired

    def _transition_run(
        self,
        run: AutomationRun,
        *,
        status: AutomationRunStatus,
        detail: str,
        terminal: bool = False,
    ) -> AutomationRun:
        if terminal != status.is_terminal:
            raise ValueError("terminal flag must match Automation Run status")
        now = _not_before(run.updated_at)
        desired = replace(
            run,
            status=status,
            detail=detail[:2_000],
            updated_at=now,
            completed_at=now if terminal else None,
        )
        return self._persist_run_state(run, desired)

    def _observe_run(self, run_id: str, event: Event) -> None:
        if event.msg.type == "turn_started":
            self._mark_run_running(run_id)

    def _mark_run_running(self, run_id: str) -> None:
        with self.database.read() as connection:
            current = AutomationRunRepository(connection).get(run_id)
        if current is None or current.status.is_terminal:
            return
        now = _not_before(current.updated_at)
        desired = replace(
            current,
            status=AutomationRunStatus.RUNNING,
            detail="",
            started_at=current.started_at or max(current.created_at, now),
            updated_at=now,
            completed_at=None,
        )
        self._persist_run_state(current, desired)

    def _wake_scheduler(self) -> None:
        if self._scheduler is not None:
            self._scheduler.wake()

    def _read_definition(
        self,
        automation_id: str,
        *,
        include_retired: bool,
    ) -> Automation:
        with self.database.read() as connection:
            definition = AutomationRepository(connection).get(
                automation_id,
                include_retired=include_retired,
            )
        if definition is None:
            raise AutomationNotFoundError(f"automation not found: {automation_id}")
        return definition


def _run_from_turn(
    run: AutomationRun,
    turn: Turn,
    *,
    terminal_from_turn: bool = True,
) -> AutomationRun:
    if turn.status is TurnStatus.QUEUED:
        status = AutomationRunStatus.QUEUED
    elif turn.status is TurnStatus.RUNNING:
        status = AutomationRunStatus.RUNNING
    elif turn.status is TurnStatus.WAITING_APPROVAL:
        status = AutomationRunStatus.WAITING
    elif not terminal_from_turn:
        status = AutomationRunStatus.BLOCKED
    elif turn.status is TurnStatus.COMPLETED:
        status = AutomationRunStatus.COMPLETED
    elif turn.status is TurnStatus.INTERRUPTED:
        status = AutomationRunStatus.INTERRUPTED
    else:
        status = AutomationRunStatus.FAILED
    detail = (
        ""
        if status
        in {
            AutomationRunStatus.QUEUED,
            AutomationRunStatus.RUNNING,
            AutomationRunStatus.WAITING,
        }
        else turn.error_message or turn.stop_reason or run.detail
    )
    return replace(
        run,
        status=status,
        started_at=run.started_at
        or (
            max(run.created_at, turn.started_at)
            if turn.started_at is not None
            else None
        ),
        completed_at=(
            max(run.created_at, turn.completed_at)
            if status.is_terminal
            and terminal_from_turn
            and turn.completed_at is not None
            else None
        ),
        detail=detail[:2_000],
        updated_at=max(
            run.updated_at,
            turn.completed_at or turn.started_at or run.updated_at,
        ),
    )


def _clean_name(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise InvalidArgumentError("automation name must not be empty")
    if len(clean) > MAX_AUTOMATION_NAME:
        raise InvalidArgumentError(
            f"automation name must be at most {MAX_AUTOMATION_NAME} characters"
        )
    return clean


def _clean_prompt(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise InvalidArgumentError("automation prompt must not be empty")
    if len(clean) > MAX_AUTOMATION_PROMPT:
        raise InvalidArgumentError(
            f"automation prompt must be at most {MAX_AUTOMATION_PROMPT} characters"
        )
    return clean


def _validated_interval(
    kind: AutomationScheduleKind,
    interval_seconds: int | None,
) -> int | None:
    if kind is AutomationScheduleKind.MANUAL:
        return None
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS
    ):
        raise InvalidArgumentError("intervalSeconds must be between 60 and 31622400")
    return interval_seconds


def _validate_page(*, limit: int, offset: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_AUTOMATION_PAGE_SIZE
    ):
        raise InvalidArgumentError("automation page limit must be 1-500")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise InvalidArgumentError("automation page offset must be non-negative")


def _validated_activation_status(
    status: AutomationActivationStatus | None,
) -> AutomationStatus | None:
    if status is None:
        return None
    try:
        activation_status = AutomationActivationStatus(status)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError("status must be enabled or paused") from exc
    return AutomationStatus(activation_status.value)


def _manual_occurrence_key(request_id: str | None) -> str:
    if request_id is None:
        return new_id("areq")
    clean = request_id.strip()
    if not clean:
        raise InvalidArgumentError("requestId must not be empty")
    if len(clean) > MAX_OCCURRENCE_KEY:
        raise InvalidArgumentError(
            f"requestId must be at most {MAX_OCCURRENCE_KEY} characters"
        )
    return clean


def _scheduled_occurrence_key(scheduled_for: datetime) -> str:
    normalized = scheduled_for.astimezone(UTC)
    return f"scheduled:{dump_datetime(normalized)}"


def _not_before(value: datetime) -> datetime:
    """Return a wall-clock timestamp without moving durable state backwards."""

    return max(value, utc_now())
