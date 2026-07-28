"""Minimal Thread-scoped Goal state.

A Goal extends the ordinary Thread/Turn lifecycle.  It does not introduce a
second execution unit, evaluator, or attempt ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
)
from core.skills.models import MAX_SELECTED_SKILLS, SkillSelection


GOAL_OBJECTIVE_INPUT_MAX_CHARS = 4_000
GOAL_OBJECTIVE_MAX_CHARS = 16_384
GOAL_OUTCOME_EVIDENCE_MAX_ITEMS = 12
GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS = 240
GOAL_OUTCOME_REASON_MAX_CHARS = 2_000


class ThreadGoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    BUDGET_LIMITED = "budget_limited"
    COMPLETE = "complete"

    @property
    def automatically_continues(self) -> bool:
        return self is ThreadGoalStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self is ThreadGoalStatus.COMPLETE


class GoalDecisionSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    RUNTIME = "runtime"
    MIGRATION = "migration"


@dataclass(frozen=True, slots=True)
class GoalEvidenceRef:
    item_id: str
    turn_id: str
    kind: str
    status: str
    summary: str

    def __post_init__(self) -> None:
        require_prefixed_id(self.item_id, "item")
        require_prefixed_id(self.turn_id, "turn")
        require_non_empty(self.kind, "kind")
        require_non_empty(self.status, "status")
        require_non_empty(self.summary, "summary")
        if len(self.summary) > GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS:
            raise ValueError(
                "Goal evidence summary may contain at most "
                f"{GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS} characters"
            )


@dataclass(frozen=True, slots=True)
class GoalOutcome:
    status: ThreadGoalStatus
    reason: str
    source: GoalDecisionSource
    decided_by_turn_id: str | None
    decided_at: datetime
    evidence_refs: tuple[GoalEvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {
            ThreadGoalStatus.COMPLETE,
            ThreadGoalStatus.BLOCKED,
        }:
            raise ValueError("Goal outcome status must be complete or blocked")
        require_non_empty(self.reason, "reason")
        if len(self.reason) > GOAL_OUTCOME_REASON_MAX_CHARS:
            raise ValueError(
                "Goal outcome reason may contain at most "
                f"{GOAL_OUTCOME_REASON_MAX_CHARS} characters"
            )
        if self.decided_by_turn_id is not None:
            require_prefixed_id(self.decided_by_turn_id, "turn")
        require_aware(self.decided_at, "decided_at")
        if len(self.evidence_refs) > GOAL_OUTCOME_EVIDENCE_MAX_ITEMS:
            raise ValueError(
                "Goal outcome has too many evidence references: "
                f"{len(self.evidence_refs)}"
            )


_USER_TRANSITIONS: dict[ThreadGoalStatus, frozenset[ThreadGoalStatus]] = {
    ThreadGoalStatus.ACTIVE: frozenset({ThreadGoalStatus.PAUSED}),
    ThreadGoalStatus.PAUSED: frozenset({ThreadGoalStatus.ACTIVE}),
    ThreadGoalStatus.BLOCKED: frozenset({ThreadGoalStatus.ACTIVE}),
    ThreadGoalStatus.BUDGET_LIMITED: frozenset({ThreadGoalStatus.ACTIVE}),
    ThreadGoalStatus.COMPLETE: frozenset({ThreadGoalStatus.ACTIVE}),
}


@dataclass(frozen=True, slots=True)
class ThreadGoal:
    thread_id: str
    objective: str
    status: ThreadGoalStatus = ThreadGoalStatus.ACTIVE
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    skill_ids: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("goal"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "goal")
        require_non_empty(self.thread_id, "thread_id")
        require_non_empty(self.objective, "objective")
        if len(self.objective) > GOAL_OBJECTIVE_MAX_CHARS:
            raise ValueError(
                f"objective may contain at most {GOAL_OBJECTIVE_MAX_CHARS} characters"
            )
        if isinstance(self.token_budget, bool) or (
            self.token_budget is not None and self.token_budget < 1
        ):
            raise ValueError("token_budget must be positive when provided")
        if isinstance(self.tokens_used, bool) or self.tokens_used < 0:
            raise ValueError("tokens_used cannot be negative")
        if isinstance(self.time_used_seconds, bool) or self.time_used_seconds < 0:
            raise ValueError("time_used_seconds cannot be negative")
        if len(self.skill_ids) > MAX_SELECTED_SKILLS:
            raise ValueError(f"a Goal may select at most {MAX_SELECTED_SKILLS} Skills")
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("skill_ids must be unique")
        try:
            for skill_id in self.skill_ids:
                SkillSelection(skill_id=skill_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("skill_ids must contain opaque sk_ identifiers") from exc
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

    def edit(
        self,
        objective: str,
        *,
        token_budget: int | None,
        skill_ids: tuple[str, ...],
        now: datetime | None = None,
    ) -> "ThreadGoal":
        """Update the same logical Goal without creating a hidden revision."""

        next_status = self.status
        if (
            self.status is ThreadGoalStatus.ACTIVE
            and token_budget is not None
            and self.tokens_used >= token_budget
        ):
            next_status = ThreadGoalStatus.BUDGET_LIMITED
        return replace(
            self,
            objective=objective.strip(),
            status=next_status,
            token_budget=token_budget,
            skill_ids=skill_ids,
            updated_at=now or utc_now(),
        )

    def user_transition(
        self,
        status: ThreadGoalStatus,
        *,
        now: datetime | None = None,
    ) -> "ThreadGoal":
        """Apply an explicit user-controlled lifecycle transition."""

        if status is self.status:
            return self
        if status not in _USER_TRANSITIONS[self.status]:
            raise ValueError(
                f"invalid user Goal transition: {self.status.value} -> {status.value}"
            )
        if (
            status is ThreadGoalStatus.ACTIVE
            and self.token_budget is not None
            and self.tokens_used >= self.token_budget
        ):
            raise ValueError(
                "increase or remove the exhausted token budget before resuming"
            )
        return replace(self, status=status, updated_at=now or utc_now())

    def agent_transition(
        self,
        status: ThreadGoalStatus,
        *,
        now: datetime | None = None,
    ) -> "ThreadGoal":
        """Apply the only terminal states the active Agent may request."""

        if status not in {ThreadGoalStatus.COMPLETE, ThreadGoalStatus.BLOCKED}:
            raise ValueError("the Agent may only mark a Goal complete or blocked")
        if self.status is not ThreadGoalStatus.ACTIVE:
            raise ValueError("only an active Goal accepts an Agent status update")
        return replace(self, status=status, updated_at=now or utc_now())

    def block_after_error(self, *, now: datetime | None = None) -> "ThreadGoal":
        """Stop automatic continuation after a terminal runtime failure."""

        if self.status is not ThreadGoalStatus.ACTIVE:
            return self
        return replace(
            self,
            status=ThreadGoalStatus.BLOCKED,
            updated_at=now or utc_now(),
        )

    def add_usage(
        self,
        *,
        tokens: int,
        elapsed_seconds: int,
        now: datetime | None = None,
    ) -> "ThreadGoal":
        if isinstance(tokens, bool) or tokens < 0:
            raise ValueError("tokens must not be negative")
        if isinstance(elapsed_seconds, bool) or elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must not be negative")
        next_tokens = self.tokens_used + tokens
        next_status = self.status
        if (
            self.status is ThreadGoalStatus.ACTIVE
            and self.token_budget is not None
            and next_tokens >= self.token_budget
        ):
            next_status = ThreadGoalStatus.BUDGET_LIMITED
        return replace(
            self,
            status=next_status,
            tokens_used=next_tokens,
            time_used_seconds=self.time_used_seconds + elapsed_seconds,
            updated_at=now or utc_now(),
        )


__all__ = [
    "GOAL_OUTCOME_EVIDENCE_MAX_ITEMS",
    "GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS",
    "GOAL_OUTCOME_REASON_MAX_CHARS",
    "GOAL_OBJECTIVE_INPUT_MAX_CHARS",
    "GOAL_OBJECTIVE_MAX_CHARS",
    "GoalDecisionSource",
    "GoalEvidenceRef",
    "GoalOutcome",
    "ThreadGoal",
    "ThreadGoalStatus",
]
