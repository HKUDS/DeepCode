"""Turn lifecycle model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
)


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            TurnStatus.COMPLETED,
            TurnStatus.FAILED,
            TurnStatus.INTERRUPTED,
        }


@dataclass(frozen=True, slots=True)
class Turn:
    thread_id: str
    ordinal: int
    prompt: str
    status: TurnStatus = TurnStatus.QUEUED
    stop_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    id: str = field(default_factory=lambda: new_id("turn"))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "turn")
        require_non_empty(self.thread_id, "thread_id")
        require_non_empty(self.prompt, "prompt")
        if self.ordinal < 1:
            raise ValueError("ordinal must be positive")
        if self.started_at is not None:
            require_aware(self.started_at, "started_at")
        if self.completed_at is not None:
            require_aware(self.completed_at, "completed_at")
        if self.status.is_terminal and self.completed_at is None:
            raise ValueError("terminal turns require completed_at")
        if not self.status.is_terminal and self.completed_at is not None:
            raise ValueError("non-terminal turns cannot have completed_at")
        if self.status is TurnStatus.FAILED and not self.error_code:
            raise ValueError("failed turns require error_code")
        if self.status is not TurnStatus.FAILED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("only failed turns may carry errors")
