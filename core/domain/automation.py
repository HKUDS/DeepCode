"""Durable Desktop automation definitions and execution records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
)


class AutomationStatus(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"


class AutomationScheduleKind(StrEnum):
    MANUAL = "manual"
    INTERVAL = "interval"


class AutomationTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class AutomationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in {
            AutomationRunStatus.COMPLETED,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.INTERRUPTED,
            AutomationRunStatus.SKIPPED,
        }


@dataclass(frozen=True, slots=True)
class Automation:
    project_id: str
    thread_id: str
    name: str
    prompt: str
    schedule_kind: AutomationScheduleKind
    status: AutomationStatus = AutomationStatus.ENABLED
    interval_seconds: int | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("auto"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "auto")
        require_prefixed_id(self.project_id, "proj")
        require_non_empty(self.thread_id, "thread_id")
        require_non_empty(self.name, "name")
        require_non_empty(self.prompt, "prompt")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.last_run_at is not None:
            require_aware(self.last_run_at, "last_run_at")
        if self.next_run_at is not None:
            require_aware(self.next_run_at, "next_run_at")
        if self.schedule_kind is AutomationScheduleKind.MANUAL:
            if self.interval_seconds is not None or self.next_run_at is not None:
                raise ValueError("manual automations cannot have an interval")
            return
        if self.interval_seconds is None or self.interval_seconds < 60:
            raise ValueError("interval automations require at least 60 seconds")
        if self.status is AutomationStatus.ENABLED and self.next_run_at is None:
            raise ValueError("enabled interval automations require next_run_at")


@dataclass(frozen=True, slots=True)
class AutomationRun:
    automation_id: str
    thread_id: str
    trigger: AutomationTrigger
    status: AutomationRunStatus
    scheduled_for: datetime
    turn_id: str | None = None
    detail: str = ""
    id: str = field(default_factory=lambda: new_id("arun"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "arun")
        require_prefixed_id(self.automation_id, "auto")
        require_non_empty(self.thread_id, "thread_id")
        if self.turn_id is not None:
            require_prefixed_id(self.turn_id, "turn")
        require_aware(self.scheduled_for, "scheduled_for")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
        if self.status.is_terminal and self.completed_at is None:
            raise ValueError("terminal automation runs require completed_at")
        if not self.status.is_terminal and self.completed_at is not None:
            raise ValueError("non-terminal automation runs cannot have completed_at")
        if self.completed_at is not None:
            require_aware(self.completed_at, "completed_at")
