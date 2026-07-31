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
    utc_now,
)
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_profile import ExecutionProfile
from core.domain.runtime_coordination import ExecutionClass
from core.skills.models import MAX_SELECTED_SKILLS, SkillSelection


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


class TurnExecutor(StrEnum):
    """Typed runtime responsible for executing one durable Turn."""

    AGENT = "agent"
    WORKFLOW = "workflow"


@dataclass(frozen=True, slots=True)
class Turn:
    thread_id: str
    ordinal: int
    prompt: str
    skill_ids: tuple[str, ...] = ()
    execution_profile: ExecutionProfile | None = None
    execution_permission_mode: ExecutionPermissionMode | None = None
    goal_id: str | None = None
    executor: TurnExecutor = TurnExecutor.AGENT
    execution_class: ExecutionClass = ExecutionClass.INTERACTIVE
    home_worker_id: str | None = None
    execution_owner_id: str | None = None
    execution_epoch: int = 0
    enqueued_at: datetime = field(default_factory=utc_now)
    cancel_requested_at: datetime | None = None
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
        if len(self.skill_ids) > MAX_SELECTED_SKILLS:
            raise ValueError(f"a turn may select at most {MAX_SELECTED_SKILLS} skills")
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("skill_ids must be unique")
        try:
            for skill_id in self.skill_ids:
                SkillSelection(skill_id=skill_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("skill_ids must contain opaque sk_ identifiers") from exc
        if self.execution_permission_mode is not None and not isinstance(
            self.execution_permission_mode,
            ExecutionPermissionMode,
        ):
            raise TypeError(
                "execution_permission_mode must be an ExecutionPermissionMode"
            )
        if self.goal_id is not None:
            require_prefixed_id(str(self.goal_id), "goal")
        if self.home_worker_id is not None:
            require_prefixed_id(self.home_worker_id, "worker")
        if self.execution_owner_id is not None:
            require_prefixed_id(self.execution_owner_id, "worker")
        if isinstance(self.execution_epoch, bool) or self.execution_epoch < 0:
            raise ValueError("execution_epoch must be non-negative")
        if self.execution_owner_id is not None and self.execution_epoch < 1:
            raise ValueError("an owned Turn requires a positive execution_epoch")
        if self.status.is_terminal and self.execution_owner_id is not None:
            raise ValueError("terminal turns cannot retain an execution owner")
        require_aware(self.enqueued_at, "enqueued_at")
        if self.cancel_requested_at is not None:
            require_aware(self.cancel_requested_at, "cancel_requested_at")
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
