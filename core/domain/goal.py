"""Session-scoped goals that coordinate ordinary Turns."""

from __future__ import annotations

import math
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

GOAL_OBJECTIVE_MAX_CHARS = 4_000
GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS = 20
GOAL_ACCEPTANCE_CRITERION_MAX_CHARS = 500
GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS = 1_800
GOAL_EVALUATOR_MIN_TOKENS = 64


@dataclass(frozen=True, slots=True)
class GoalPolicyDefaults:
    """Built-in policy used only when neither user nor project config overrides it."""

    max_attempts: int | None = 20
    max_tokens: int | None = None
    max_elapsed_seconds: int | None = 28_800
    stall_threshold: int = 3
    evaluator_retry_limit: int = 1
    evaluator_max_tokens: int = 1_024
    evaluator_temperature: float = 0.0
    verification_timeout_seconds: int = 300


DEFAULT_GOAL_POLICY = GoalPolicyDefaults()


class GoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    USAGE_LIMITED = "usage_limited"
    BUDGET_LIMITED = "budget_limited"
    COMPLETED = "completed"

    @property
    def automatically_continues(self) -> bool:
        return self is GoalStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self in {GoalStatus.BUDGET_LIMITED, GoalStatus.COMPLETED}


class GoalAttemptStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            GoalAttemptStatus.COMPLETED,
            GoalAttemptStatus.FAILED,
            GoalAttemptStatus.INTERRUPTED,
        }


class GoalVerdict(StrEnum):
    COMPLETE = "complete"
    CONTINUE = "continue"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class GoalBudget:
    max_attempts: int | None = None
    max_tokens: int | None = None
    max_elapsed_seconds: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_attempts", self.max_attempts),
            ("max_tokens", self.max_tokens),
            ("max_elapsed_seconds", self.max_elapsed_seconds),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be positive when provided")


@dataclass(frozen=True, slots=True)
class Goal:
    thread_id: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    status: GoalStatus = GoalStatus.ACTIVE
    revision: int = 1
    definition_revision: int = 1
    tokens_used: int = 0
    elapsed_seconds: int = 0
    budget: GoalBudget = field(default_factory=GoalBudget)
    skill_ids: tuple[str, ...] = ()
    verification_command_id: str | None = None
    verification_timeout_seconds: int = DEFAULT_GOAL_POLICY.verification_timeout_seconds
    evaluator_connection_id: str | None = None
    evaluator_model_id: str | None = None
    evaluator_max_tokens: int = DEFAULT_GOAL_POLICY.evaluator_max_tokens
    evaluator_temperature: float = DEFAULT_GOAL_POLICY.evaluator_temperature
    evaluator_retry_limit: int = DEFAULT_GOAL_POLICY.evaluator_retry_limit
    stall_threshold: int = DEFAULT_GOAL_POLICY.stall_threshold
    last_verdict: GoalVerdict | None = None
    last_reason: str | None = None
    id: str = field(default_factory=lambda: new_id("goal"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    active_since: datetime | None = field(default_factory=utc_now)
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "goal")
        require_non_empty(self.thread_id, "thread_id")
        require_non_empty(self.objective, "objective")
        if len(self.objective) > GOAL_OBJECTIVE_MAX_CHARS:
            raise ValueError(
                f"objective may contain at most {GOAL_OBJECTIVE_MAX_CHARS} characters"
            )
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.definition_revision < 1:
            raise ValueError("definition_revision must be positive")
        if self.definition_revision > self.revision:
            raise ValueError("definition_revision cannot exceed revision")
        if self.tokens_used < 0:
            raise ValueError("tokens_used cannot be negative")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds cannot be negative")
        if len(self.acceptance_criteria) > GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS:
            raise ValueError(
                "a goal may have at most "
                f"{GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS} acceptance criteria"
            )
        if any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria must not be empty")
        if any(
            len(item) > GOAL_ACCEPTANCE_CRITERION_MAX_CHARS
            for item in self.acceptance_criteria
        ):
            raise ValueError(
                "each acceptance criterion may contain at most "
                f"{GOAL_ACCEPTANCE_CRITERION_MAX_CHARS} characters"
            )
        if len(set(self.skill_ids)) != len(self.skill_ids):
            raise ValueError("skill_ids must be unique")
        if (
            self.verification_command_id is not None
            and not self.verification_command_id.strip()
        ):
            raise ValueError("verification_command_id must not be empty")
        if not (
            1
            <= self.verification_timeout_seconds
            <= GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS
        ):
            raise ValueError(
                "verification_timeout_seconds must be between 1 and "
                f"{GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS}"
            )
        if (
            self.evaluator_connection_id is not None
            and not self.evaluator_connection_id.strip()
        ):
            raise ValueError("evaluator_connection_id must not be empty")
        if self.evaluator_model_id is not None and not self.evaluator_model_id.strip():
            raise ValueError("evaluator_model_id must not be empty")
        if self.evaluator_max_tokens < GOAL_EVALUATOR_MIN_TOKENS:
            raise ValueError(
                f"evaluator_max_tokens must be at least {GOAL_EVALUATOR_MIN_TOKENS}"
            )
        if (
            not math.isfinite(self.evaluator_temperature)
            or self.evaluator_temperature < 0
        ):
            raise ValueError(
                "evaluator_temperature must be a finite non-negative number"
            )
        if self.evaluator_retry_limit < 0:
            raise ValueError("evaluator_retry_limit cannot be negative")
        if self.stall_threshold < 1:
            raise ValueError("stall_threshold must be positive")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.active_since is not None:
            require_aware(self.active_since, "active_since")
        if self.completed_at is not None:
            require_aware(self.completed_at, "completed_at")
        if self.status is GoalStatus.ACTIVE and self.active_since is None:
            raise ValueError("active goals require active_since")
        if self.status is not GoalStatus.ACTIVE and self.active_since is not None:
            raise ValueError("only active goals may carry active_since")
        if self.status is GoalStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed goals require completed_at")
        if self.status is not GoalStatus.COMPLETED and self.completed_at is not None:
            raise ValueError("only completed goals may carry completed_at")


@dataclass(frozen=True, slots=True)
class GoalAttempt:
    goal_id: str
    goal_revision: int
    ordinal: int
    status: GoalAttemptStatus
    turn_id: str | None = None
    id: str = field(default_factory=lambda: new_id("gatt"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "gatt")
        require_prefixed_id(self.goal_id, "goal")
        if self.goal_revision < 1:
            raise ValueError("goal_revision must be positive")
        if self.ordinal < 1:
            raise ValueError("ordinal must be positive")
        if self.turn_id is not None:
            require_prefixed_id(self.turn_id, "turn")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.completed_at is not None:
            require_aware(self.completed_at, "completed_at")
        if self.status.is_terminal and self.completed_at is None:
            raise ValueError("terminal attempts require completed_at")
        if not self.status.is_terminal and self.completed_at is not None:
            raise ValueError("non-terminal attempts cannot carry completed_at")


@dataclass(frozen=True, slots=True)
class GoalEvaluation:
    goal_id: str
    goal_revision: int
    attempt_id: str
    turn_id: str
    verdict: GoalVerdict
    reason: str
    evidence_refs: tuple[str, ...] = ()
    evaluator_provider: str | None = None
    evaluator_model: str | None = None
    tokens_used: int = 0
    id: str = field(default_factory=lambda: new_id("geval"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "geval")
        require_prefixed_id(self.goal_id, "goal")
        require_prefixed_id(self.attempt_id, "gatt")
        require_prefixed_id(self.turn_id, "turn")
        require_non_empty(self.reason, "reason")
        if self.goal_revision < 1:
            raise ValueError("goal_revision must be positive")
        if self.tokens_used < 0:
            raise ValueError("tokens_used cannot be negative")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class GoalRecord:
    goal: Goal
    attempts: tuple[GoalAttempt, ...] = ()
    evaluations: tuple[GoalEvaluation, ...] = ()

    @property
    def latest_attempt(self) -> GoalAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def latest_evaluation(self) -> GoalEvaluation | None:
        return self.evaluations[-1] if self.evaluations else None


__all__ = [
    "Goal",
    "DEFAULT_GOAL_POLICY",
    "GoalAttempt",
    "GoalAttemptStatus",
    "GoalBudget",
    "GoalEvaluation",
    "GoalRecord",
    "GoalStatus",
    "GoalVerdict",
    "GoalPolicyDefaults",
]
