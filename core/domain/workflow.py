"""Durable long-running workflow state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    JsonObject,
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
    validate_json_object,
)


class WorkflowStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    thread_id: str
    turn_id: str
    kind: str
    status: WorkflowStatus = WorkflowStatus.QUEUED
    input: JsonObject = field(default_factory=dict)
    result: JsonObject = field(default_factory=dict)
    attempt: int = 1
    retry_of: str | None = None
    current_stage: str | None = None
    progress_current: int = 0
    progress_total: int | None = None
    checkpoint: JsonObject = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("wfr"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "wfr")
        require_non_empty(self.thread_id, "thread_id")
        require_prefixed_id(self.turn_id, "turn")
        require_non_empty(self.kind, "kind")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.retry_of is not None:
            require_prefixed_id(self.retry_of, "wfr")
        if self.progress_current < 0:
            raise ValueError("progress_current cannot be negative")
        if self.progress_total is not None:
            if self.progress_total < 0 or self.progress_current > self.progress_total:
                raise ValueError("invalid workflow progress")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        validate_json_object(self.input, "input")
        validate_json_object(self.result, "result")
        validate_json_object(self.checkpoint, "checkpoint")
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
        if self.status.is_terminal and self.completed_at is None:
            raise ValueError("terminal workflows require completed_at")
        if not self.status.is_terminal and self.completed_at is not None:
            raise ValueError("non-terminal workflows cannot have completed_at")
        if self.completed_at is not None:
            require_aware(self.completed_at, "completed_at")
        if self.status is WorkflowStatus.FAILED and not self.error_code:
            raise ValueError("failed workflows require error_code")
        if self.status is not WorkflowStatus.FAILED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("only failed workflows may carry an error")
