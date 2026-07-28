"""Durable, App-Server-owned scheduling over canonical Goal Threads."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from core.application.errors import (
    AutomationNotFoundError,
    ConflictError,
    InvalidArgumentError,
    ProjectNotTrustedError,
    TurnAlreadyRunningError,
)
from core.application.event_service import EventBroker
from core.application.project_service import ProjectService
from core.application.thread_service import ThreadService
from core.application.turn_service import TurnService, TurnSnapshot
from core.application.views import automation_run_view, automation_view
from core.domain.automation import (
    Automation,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.common import utc_now
from core.domain.message_provenance import ClientSurface, TurnInputSource
from core.domain.event import DomainEvent
from core.domain.project import TrustState
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn, TurnStatus
from core.events import Event
from core.persistence.automation_repository import (
    AutomationRepository,
    AutomationRunRepository,
)
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.persistence.thread_repository import ThreadRepository


logger = logging.getLogger(__name__)
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 366 * 24 * 60 * 60
MAX_AUTOMATION_NAME = 120
MAX_AUTOMATION_PROMPT = 16_384


@dataclass(frozen=True, slots=True)
class AutomationInventory:
    automations: tuple[Automation, ...]
    latest_runs: tuple[AutomationRun, ...]
    scheduler_active: bool


@dataclass(frozen=True, slots=True)
class AutomationCreation:
    automation: Automation
    thread: Thread


@dataclass(frozen=True, slots=True)
class AutomationExecution:
    run: AutomationRun
    turn: Turn | None


class AutomationService:
    """Persist schedules and submit due work through the normal TurnService."""

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        projects: ProjectService,
        threads: ThreadService,
        turns: TurnService,
    ) -> None:
        self.database = database
        self.broker = broker
        self.projects = projects
        self.threads = threads
        self.turns = turns
        self._operation_lock = threading.RLock()
        self._scheduler_lock = threading.Lock()
        self._scheduler_wake = threading.Event()
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None

    @property
    def scheduler_active(self) -> bool:
        with self._scheduler_lock:
            return bool(
                self._scheduler_thread is not None
                and self._scheduler_thread.is_alive()
                and not self._scheduler_stop.is_set()
            )

    def start_scheduler(self) -> None:
        with self._scheduler_lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            self._scheduler_wake.clear()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                name="deepcode-automation-scheduler",
                daemon=True,
            )
            self._scheduler_thread.start()

    def close(self) -> None:
        self._scheduler_stop.set()
        self._scheduler_wake.set()
        with self._scheduler_lock:
            thread = self._scheduler_thread
            self._scheduler_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def list(self, project_id: str | None = None) -> AutomationInventory:
        if project_id is not None:
            self.projects.read(project_id)
        self.reconcile_runs()
        with self.database.read() as connection:
            jobs = AutomationRepository(connection).list(project_id=project_id)
            run_repo = AutomationRunRepository(connection)
            latest = tuple(
                run
                for job in jobs
                if (run := run_repo.latest_for_automation(job.id)) is not None
            )
        return AutomationInventory(
            automations=tuple(jobs),
            latest_runs=latest,
            scheduler_active=self.scheduler_active,
        )

    def read(self, automation_id: str) -> Automation:
        with self.database.read() as connection:
            automation = AutomationRepository(connection).get(automation_id)
        if automation is None:
            raise AutomationNotFoundError(f"automation not found: {automation_id}")
        return automation

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
        project = self.projects.read(project_id)
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before an automation can be created"
            )
        interval = _validated_interval(schedule_kind, interval_seconds)
        now = utc_now()
        status = AutomationStatus.ENABLED if enabled else AutomationStatus.PAUSED
        next_run_at = (
            now + timedelta(seconds=interval)
            if schedule_kind is AutomationScheduleKind.INTERVAL
            and status is AutomationStatus.ENABLED
            and interval is not None
            else None
        )
        thread = self.threads.start(
            project_id,
            title=clean_name,
            mode=ThreadMode.GOAL,
        )
        automation = Automation(
            project_id=project_id,
            thread_id=thread.id,
            name=clean_name,
            prompt=clean_prompt,
            schedule_kind=schedule_kind,
            status=status,
            interval_seconds=interval,
            next_run_at=next_run_at,
            created_at=now,
            updated_at=now,
        )
        try:
            with self.database.transaction() as connection:
                AutomationRepository(connection).add(automation)
                event = EventRepository(connection).append(
                    thread_id=thread.id,
                    type="automation.updated",
                    payload={"automation": automation_view(automation)},
                )
        except BaseException:
            self._rollback_created_thread(thread.id)
            raise
        self.broker.publish(event)
        self._scheduler_wake.set()
        return AutomationCreation(automation, thread)

    def update(
        self,
        automation_id: str,
        *,
        name: str | None = None,
        prompt: str | None = None,
        status: AutomationStatus | None = None,
        schedule_kind: AutomationScheduleKind | None = None,
        interval_seconds: int | None = None,
    ) -> Automation:
        with self._operation_lock:
            current = self.read(automation_id)
            next_status = status or current.status
            next_kind = schedule_kind or current.schedule_kind
            next_interval = _validated_interval(
                next_kind,
                (
                    interval_seconds
                    if interval_seconds is not None
                    else current.interval_seconds
                ),
            )
            if next_status is AutomationStatus.ENABLED:
                project = self.projects.read(current.project_id)
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
                next_run_at = now + timedelta(seconds=next_interval)
            else:
                next_run_at = current.next_run_at
            updated = replace(
                current,
                name=_clean_name(name) if name is not None else current.name,
                prompt=(
                    _clean_prompt(prompt) if prompt is not None else current.prompt
                ),
                status=next_status,
                schedule_kind=next_kind,
                interval_seconds=next_interval,
                next_run_at=next_run_at,
                updated_at=now,
            )
            with self.database.transaction() as connection:
                AutomationRepository(connection).update(updated)
                event = EventRepository(connection).append(
                    thread_id=updated.thread_id,
                    type="automation.updated",
                    payload={"automation": automation_view(updated)},
                )
            self.broker.publish(event)
            self._scheduler_wake.set()
            return updated

    def remove(self, automation_id: str) -> bool:
        with self._operation_lock:
            automation = self.read(automation_id)
            with self.database.transaction() as connection:
                runs = AutomationRunRepository(connection)
                latest = runs.latest_for_automation(automation_id)
                if latest is not None and not latest.status.is_terminal:
                    raise ConflictError(
                        "cannot remove an automation while its Goal Turn is active"
                    )
                removed = AutomationRepository(connection).remove(automation_id)
                event = EventRepository(connection).append(
                    thread_id=automation.thread_id,
                    type="automation.updated",
                    payload={"automationId": automation_id, "removed": removed},
                )
            if removed:
                self.broker.publish(event)
                self._scheduler_wake.set()
            return removed

    def list_runs(
        self,
        automation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[AutomationRun, ...]:
        if not 1 <= limit <= 500:
            raise InvalidArgumentError("automation run limit must be 1-500")
        self.read(automation_id)
        self.reconcile_runs()
        with self.database.read() as connection:
            runs = AutomationRunRepository(connection).list_for_automation(
                automation_id,
                limit=limit,
            )
        return tuple(runs)

    def run_now(self, automation_id: str) -> AutomationExecution:
        automation = self.read(automation_id)
        project = self.projects.read(automation.project_id)
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before an automation can run"
            )
        execution = self._trigger(
            automation,
            trigger=AutomationTrigger.MANUAL,
            scheduled_for=utc_now(),
        )
        assert execution is not None
        return execution

    def run_due(self, now: datetime | None = None) -> tuple[AutomationRun, ...]:
        current_time = now or utc_now()
        with self._operation_lock:
            self.reconcile_runs()
            with self.database.read() as connection:
                due = AutomationRepository(connection).list_due(current_time)
            executions: list[AutomationExecution] = []
            for automation in due:
                execution = self._trigger(
                    automation,
                    trigger=AutomationTrigger.SCHEDULED,
                    scheduled_for=automation.next_run_at or current_time,
                    now=current_time,
                )
                if execution is not None:
                    executions.append(execution)
        return tuple(execution.run for execution in executions)

    def reconcile_runs(self) -> int:
        with self._operation_lock:
            return self._reconcile_runs_locked()

    def _reconcile_runs_locked(self) -> int:
        events: list[DomainEvent] = []
        changed = 0
        with self.database.transaction() as connection:
            runs = AutomationRunRepository(connection)
            turns = TurnRepository(connection)
            automations = AutomationRepository(connection)
            event_repo = EventRepository(connection)
            for run in runs.list_active():
                if run.turn_id is None:
                    settled = replace(
                        run,
                        status=AutomationRunStatus.FAILED,
                        detail="Automation submission was interrupted",
                        updated_at=utc_now(),
                        completed_at=utc_now(),
                    )
                else:
                    turn = turns.get(run.turn_id)
                    if turn is None:
                        settled = replace(
                            run,
                            status=AutomationRunStatus.FAILED,
                            detail="Automation Turn record is missing",
                            updated_at=utc_now(),
                            completed_at=utc_now(),
                        )
                    else:
                        settled = _run_from_turn(run, turn)
                if settled == run:
                    continue
                runs.update(settled)
                automation = automations.get(run.automation_id)
                events.append(
                    event_repo.append(
                        thread_id=run.thread_id,
                        turn_id=settled.turn_id,
                        type="automation.updated",
                        payload={
                            **(
                                {"automation": automation_view(automation)}
                                if automation is not None
                                else {}
                            ),
                            "run": automation_run_view(settled),
                        },
                    )
                )
                changed += 1
        for event in events:
            self.broker.publish(event)
        return changed

    def _trigger(
        self,
        automation: Automation,
        *,
        trigger: AutomationTrigger,
        scheduled_for: datetime,
        now: datetime | None = None,
    ) -> AutomationExecution | None:
        with self._operation_lock:
            current_time = now or utc_now()
            current = self.read(automation.id)
            if trigger is AutomationTrigger.SCHEDULED and (
                current.status is not AutomationStatus.ENABLED
                or current.schedule_kind is not AutomationScheduleKind.INTERVAL
            ):
                raise InvalidArgumentError("automation is not scheduled")
            project = self.projects.read(current.project_id)
            if project.trust_state is not TrustState.TRUSTED:
                return self._record_without_turn(
                    current,
                    trigger=trigger,
                    scheduled_for=scheduled_for,
                    status=AutomationRunStatus.SKIPPED,
                    detail="Project is not trusted",
                    now=current_time,
                    expected_next_run_at=(
                        scheduled_for
                        if trigger is AutomationTrigger.SCHEDULED
                        else None
                    ),
                )

            run = AutomationRun(
                automation_id=current.id,
                thread_id=current.thread_id,
                trigger=trigger,
                status=AutomationRunStatus.QUEUED,
                scheduled_for=scheduled_for,
                created_at=current_time,
                updated_at=current_time,
            )
            next_run_at = current.next_run_at
            if (
                trigger is AutomationTrigger.SCHEDULED
                and current.interval_seconds is not None
            ):
                next_run_at = current_time + timedelta(seconds=current.interval_seconds)
            touched = replace(
                current,
                next_run_at=next_run_at,
                last_run_at=current_time,
                updated_at=current_time,
            )
            with self.database.transaction() as connection:
                automations = AutomationRepository(connection)
                if trigger is AutomationTrigger.SCHEDULED:
                    if not automations.claim_due(
                        touched,
                        expected_next_run_at=scheduled_for,
                    ):
                        return None
                else:
                    automations.update(touched)
                AutomationRunRepository(connection).add(run)
                event = EventRepository(connection).append(
                    thread_id=touched.thread_id,
                    type="automation.updated",
                    payload={
                        "automation": automation_view(touched),
                        "run": automation_run_view(run),
                    },
                )
            self.broker.publish(event)

            try:
                snapshot = self.turns.start(
                    touched.thread_id,
                    prompt=touched.prompt,
                    event_observer=lambda event: self._observe_run(run.id, event),
                    client_surface=ClientSurface.AUTOMATION,
                    input_source=TurnInputSource.AUTOMATION,
                )
            except TurnAlreadyRunningError:
                settled = self._settle_run(
                    run.id,
                    status=AutomationRunStatus.SKIPPED,
                    detail="Previous Goal Turn is still active",
                )
                return AutomationExecution(settled, None)
            except Exception as exc:  # noqa: BLE001 - durable failed submission
                settled = self._settle_run(
                    run.id,
                    status=AutomationRunStatus.FAILED,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                return AutomationExecution(settled, None)

            attached = self._attach_turn(run.id, snapshot)
            return AutomationExecution(attached, snapshot.turn)

    def _record_without_turn(
        self,
        automation: Automation,
        *,
        trigger: AutomationTrigger,
        scheduled_for: datetime,
        status: AutomationRunStatus,
        detail: str,
        now: datetime,
        expected_next_run_at: datetime | None = None,
    ) -> AutomationExecution | None:
        run = AutomationRun(
            automation_id=automation.id,
            thread_id=automation.thread_id,
            trigger=trigger,
            status=status,
            scheduled_for=scheduled_for,
            detail=detail,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        next_run_at = automation.next_run_at
        if trigger is AutomationTrigger.SCHEDULED and automation.interval_seconds:
            next_run_at = now + timedelta(seconds=automation.interval_seconds)
        touched = replace(
            automation,
            next_run_at=next_run_at,
            last_run_at=now,
            updated_at=now,
        )
        with self.database.transaction() as connection:
            automations = AutomationRepository(connection)
            if expected_next_run_at is not None:
                if not automations.claim_due(
                    touched,
                    expected_next_run_at=expected_next_run_at,
                ):
                    return None
            else:
                automations.update(touched)
            AutomationRunRepository(connection).add(run)
            event = EventRepository(connection).append(
                thread_id=automation.thread_id,
                type="automation.updated",
                payload={
                    "automation": automation_view(touched),
                    "run": automation_run_view(run),
                },
            )
        self.broker.publish(event)
        return AutomationExecution(run, None)

    def _attach_turn(
        self,
        run_id: str,
        snapshot: TurnSnapshot,
    ) -> AutomationRun:
        with self.database.transaction() as connection:
            runs = AutomationRunRepository(connection)
            current = runs.get(run_id)
            if current is None:
                raise AutomationNotFoundError(f"automation run not found: {run_id}")
            if current.status.is_terminal:
                attached = replace(current, turn_id=snapshot.turn.id)
            else:
                attached = _run_from_turn(
                    replace(current, turn_id=snapshot.turn.id),
                    snapshot.turn,
                )
            runs.update(attached)
            automation = AutomationRepository(connection).get(attached.automation_id)
            event = EventRepository(connection).append(
                thread_id=attached.thread_id,
                turn_id=attached.turn_id,
                type="automation.updated",
                payload={
                    **(
                        {"automation": automation_view(automation)}
                        if automation is not None
                        else {}
                    ),
                    "run": automation_run_view(attached),
                },
            )
        self.broker.publish(event)
        return attached

    def _observe_run(self, run_id: str, event: Event) -> None:
        event_type = event.msg.type
        if event_type == "turn_started":
            self._mark_run_running(run_id)
            return
        if event_type != "task_complete":
            return
        stop_reason = str(getattr(event.msg, "stop_reason", "") or "completed")
        if stop_reason == "interrupted":
            status = AutomationRunStatus.INTERRUPTED
        elif stop_reason in {"error", "empty_final_response", "busy"}:
            status = AutomationRunStatus.FAILED
        else:
            status = AutomationRunStatus.COMPLETED
        self._settle_run(run_id, status=status, detail=stop_reason)

    def _mark_run_running(self, run_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            runs = AutomationRunRepository(connection)
            current = runs.get(run_id)
            if current is None or current.status.is_terminal:
                return
            running = replace(
                current,
                status=AutomationRunStatus.RUNNING,
                started_at=current.started_at or now,
                updated_at=now,
            )
            runs.update(running)
            automation = AutomationRepository(connection).get(running.automation_id)
            event = EventRepository(connection).append(
                thread_id=running.thread_id,
                turn_id=running.turn_id,
                type="automation.updated",
                payload={
                    **(
                        {"automation": automation_view(automation)}
                        if automation is not None
                        else {}
                    ),
                    "run": automation_run_view(running),
                },
            )
        self.broker.publish(event)

    def _settle_run(
        self,
        run_id: str,
        *,
        status: AutomationRunStatus,
        detail: str,
    ) -> AutomationRun:
        if not status.is_terminal:
            raise ValueError("settle_run requires a terminal status")
        now = utc_now()
        with self.database.transaction() as connection:
            runs = AutomationRunRepository(connection)
            current = runs.get(run_id)
            if current is None:
                raise AutomationNotFoundError(f"automation run not found: {run_id}")
            if current.status.is_terminal:
                return current
            settled = replace(
                current,
                status=status,
                detail=detail[:2_000],
                updated_at=now,
                completed_at=now,
            )
            runs.update(settled)
            automation = AutomationRepository(connection).get(settled.automation_id)
            event = EventRepository(connection).append(
                thread_id=settled.thread_id,
                turn_id=settled.turn_id,
                type="automation.updated",
                payload={
                    **(
                        {"automation": automation_view(automation)}
                        if automation is not None
                        else {}
                    ),
                    "run": automation_run_view(settled),
                },
            )
        self.broker.publish(event)
        return settled

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.is_set():
            try:
                self.run_due()
            except Exception:  # noqa: BLE001 - scheduler must remain available
                logger.exception("automation scheduler pass failed")
            timeout = self._scheduler_timeout()
            self._scheduler_wake.wait(timeout)
            self._scheduler_wake.clear()

    def _scheduler_timeout(self) -> float:
        with self.database.read() as connection:
            due_at = AutomationRepository(connection).next_due_at()
        if due_at is None:
            return 30.0
        return max(0.05, min(30.0, (due_at - utc_now()).total_seconds()))

    def _rollback_created_thread(self, thread_id: str) -> None:
        try:
            with self.database.transaction() as connection:
                ThreadRepository(connection).remove(thread_id)
        finally:
            self.threads.session_store.delete_session(thread_id)


def _run_from_turn(run: AutomationRun, turn: Turn) -> AutomationRun:
    if turn.status is TurnStatus.QUEUED:
        status = AutomationRunStatus.QUEUED
    elif turn.status is TurnStatus.RUNNING:
        status = AutomationRunStatus.RUNNING
    elif turn.status is TurnStatus.WAITING_APPROVAL:
        status = AutomationRunStatus.WAITING
    elif turn.status is TurnStatus.COMPLETED:
        status = AutomationRunStatus.COMPLETED
    elif turn.status is TurnStatus.INTERRUPTED:
        status = AutomationRunStatus.INTERRUPTED
    else:
        status = AutomationRunStatus.FAILED
    detail = turn.error_message or turn.stop_reason or run.detail
    return replace(
        run,
        status=status,
        started_at=turn.started_at or run.started_at,
        completed_at=turn.completed_at if status.is_terminal else None,
        detail=detail[:2_000],
        updated_at=max(
            run.updated_at, turn.completed_at or turn.started_at or run.updated_at
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
