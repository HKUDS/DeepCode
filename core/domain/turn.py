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
from core.domain.execution_profile import ExecutionProfile
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


@dataclass(frozen=True, slots=True)
class Turn:
    thread_id: str
    ordinal: int
    prompt: str
    skill_ids: tuple[str, ...] = ()
    execution_profile: ExecutionProfile | None = None
    goal_id: str | None = None
    goal_definition_revision: int | None = None
    goal_attempt_id: str | None = None
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
        goal_fields = (
            self.goal_id,
            self.goal_definition_revision,
            self.goal_attempt_id,
        )
        if any(value is not None for value in goal_fields):
            if any(value is None for value in goal_fields):
                raise ValueError("Goal Turn fields must be provided together")
            require_prefixed_id(str(self.goal_id), "goal")
            require_prefixed_id(str(self.goal_attempt_id), "gatt")
            if int(self.goal_definition_revision or 0) < 1:
                raise ValueError("goal_definition_revision must be positive")
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
