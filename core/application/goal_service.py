"""Single-writer application service for Session-scoped Goals."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable
import logging

from core.application.errors import (
    ApplicationError,
    ConflictError,
    GoalNotFoundError,
    InvalidArgumentError,
    ThreadNotFoundError,
)
from core.config import GoalPolicyConfig, load_config_for_workspace
from core.domain.common import utc_now
from core.domain.goal import (
    GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS,
    GOAL_ACCEPTANCE_CRITERION_MAX_CHARS,
    GOAL_OBJECTIVE_MAX_CHARS,
    GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS,
    Goal,
    GoalAttempt,
    GoalAttemptStatus,
    GoalBudget,
    GoalEvaluation,
    GoalRecord,
    GoalStatus,
    GoalVerdict,
)
from core.sessions.goal_store import (
    GoalConflictError,
    GoalLedgerCorruptError,
    GoalSessionNotFoundError,
    GoalStore,
)
from core.skills.models import MAX_SELECTED_SKILLS, SkillSelection


PolicyLoader = Callable[[str], GoalPolicyConfig]
GoalUpdateSink = Callable[[str, GoalRecord | None], None]
logger = logging.getLogger(__name__)

_ATTEMPT_TRANSITIONS: dict[GoalAttemptStatus, frozenset[GoalAttemptStatus]] = {
    GoalAttemptStatus.QUEUED: frozenset(
        {
            GoalAttemptStatus.RUNNING,
            GoalAttemptStatus.FAILED,
            GoalAttemptStatus.INTERRUPTED,
        }
    ),
    GoalAttemptStatus.RUNNING: frozenset(
        {
            GoalAttemptStatus.EVALUATING,
            GoalAttemptStatus.FAILED,
            GoalAttemptStatus.INTERRUPTED,
        }
    ),
    GoalAttemptStatus.EVALUATING: frozenset(
        {
            GoalAttemptStatus.COMPLETED,
            GoalAttemptStatus.FAILED,
            GoalAttemptStatus.INTERRUPTED,
        }
    ),
    GoalAttemptStatus.COMPLETED: frozenset(),
    GoalAttemptStatus.FAILED: frozenset(),
    GoalAttemptStatus.INTERRUPTED: frozenset(),
}


class GoalService:
    """Own Goal lifecycle transitions; frontends only translate commands."""

    def __init__(
        self,
        store: GoalStore,
        *,
        policy_loader: PolicyLoader | None = None,
        update_sink: GoalUpdateSink | None = None,
    ) -> None:
        self.store = store
        self._policy_loader = policy_loader or self._load_session_policy
        self._update_sink = update_sink

    def read(self, thread_id: str) -> GoalRecord | None:
        try:
            return self.store.read(thread_id)
        except GoalSessionNotFoundError as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except GoalLedgerCorruptError as exc:
            raise ApplicationError(
                str(exc),
                user_message=(
                    "The Session Goal ledger is corrupt. DeepCode stopped Goal "
                    "execution to avoid repeating work."
                ),
            ) from exc

    def default_budget(self, thread_id: str) -> GoalBudget:
        """Resolve the configured Session policy without exposing its loader."""

        policy = self._policy_loader(thread_id)
        return GoalBudget(
            max_attempts=policy.max_attempts,
            max_tokens=policy.max_tokens,
            max_elapsed_seconds=policy.max_elapsed_seconds,
        )

    def create(
        self,
        thread_id: str,
        *,
        objective: str,
        acceptance_criteria: tuple[str, ...] = (),
        budget: GoalBudget | None = None,
        skill_ids: tuple[str, ...] = (),
        verification_command_id: str | None = None,
        verification_timeout_seconds: int | None = None,
        evaluator_connection_id: str | None = None,
        evaluator_model_id: str | None = None,
    ) -> GoalRecord:
        clean_objective = self._objective(objective)
        clean_criteria = self._criteria(acceptance_criteria)
        clean_skills = self._skills(skill_ids)
        policy = self._policy_loader(thread_id)
        resolved_budget = budget or GoalBudget(
            max_attempts=policy.max_attempts,
            max_tokens=policy.max_tokens,
            max_elapsed_seconds=policy.max_elapsed_seconds,
        )
        goal = Goal(
            thread_id=thread_id,
            objective=clean_objective,
            acceptance_criteria=clean_criteria,
            budget=resolved_budget,
            skill_ids=clean_skills,
            verification_command_id=self._clean_optional(verification_command_id),
            verification_timeout_seconds=self._verification_timeout(
                verification_timeout_seconds
                if verification_timeout_seconds is not None
                else policy.verification_timeout_seconds
            ),
            evaluator_connection_id=self._clean_optional(
                evaluator_connection_id or policy.evaluator_connection
            ),
            evaluator_model_id=self._clean_optional(
                evaluator_model_id or policy.evaluator_model
            ),
            evaluator_max_tokens=policy.evaluator_max_tokens,
            evaluator_temperature=policy.evaluator_temperature,
            evaluator_retry_limit=policy.evaluator_retry_limit,
            stall_threshold=policy.stall_threshold,
        )
        try:
            created = self.store.create(goal)
        except GoalSessionNotFoundError as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, created)
        return created

    def edit(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        objective: str,
        acceptance_criteria: tuple[str, ...],
        budget: GoalBudget,
        skill_ids: tuple[str, ...],
        verification_command_id: str | None,
        verification_timeout_seconds: int,
        evaluator_connection_id: str | None,
        evaluator_model_id: str | None,
    ) -> GoalRecord:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        if current.goal.status.is_terminal:
            raise ConflictError("a terminal Goal cannot be edited; create a new Goal")
        now = utc_now()
        updated = replace(
            current.goal,
            objective=self._objective(objective),
            acceptance_criteria=self._criteria(acceptance_criteria),
            budget=budget,
            skill_ids=self._skills(skill_ids),
            verification_command_id=self._clean_optional(verification_command_id),
            verification_timeout_seconds=self._verification_timeout(
                verification_timeout_seconds
            ),
            evaluator_connection_id=self._clean_optional(evaluator_connection_id),
            evaluator_model_id=self._clean_optional(evaluator_model_id),
            revision=current.goal.revision + 1,
            definition_revision=current.goal.definition_revision + 1,
            status=GoalStatus.ACTIVE,
            last_verdict=None,
            last_reason=None,
            active_since=current.goal.active_since or now,
            updated_at=now,
        )
        return self._update(updated, expected_revision=expected_revision)

    def pause(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        reason: str = "Paused by the user.",
    ) -> GoalRecord:
        return self._set_status(
            thread_id,
            expected_revision=expected_revision,
            status=GoalStatus.PAUSED,
            reason=reason,
        )

    def resume(self, thread_id: str, *, expected_revision: int) -> GoalRecord:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        if current.goal.status is GoalStatus.ACTIVE:
            return current
        if current.goal.status.is_terminal:
            raise ConflictError("a terminal Goal cannot be resumed")
        now = utc_now()
        updated = replace(
            current.goal,
            status=GoalStatus.ACTIVE,
            revision=current.goal.revision + 1,
            active_since=now,
            updated_at=now,
        )
        return self._update(updated, expected_revision=expected_revision)

    def block(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        reason: str,
    ) -> GoalRecord:
        return self._set_status(
            thread_id,
            expected_revision=expected_revision,
            status=GoalStatus.BLOCKED,
            reason=reason,
        )

    def clear(self, thread_id: str, *, expected_revision: int) -> None:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        try:
            self.store.clear(
                thread_id,
                goal_id=current.goal.id,
                expected_revision=expected_revision,
            )
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, None)

    def begin_attempt(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        turn_id: str | None = None,
    ) -> GoalAttempt:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        if current.goal.status is not GoalStatus.ACTIVE:
            raise ConflictError("only an active Goal can start an Attempt")
        if self._budget_exhausted(current):
            self._set_status(
                thread_id,
                expected_revision=expected_revision,
                status=GoalStatus.BUDGET_LIMITED,
                reason="Goal execution budget was exhausted.",
            )
            raise ConflictError("Goal execution budget was exhausted")
        attempt = GoalAttempt(
            goal_id=current.goal.id,
            goal_revision=current.goal.definition_revision,
            ordinal=current.attempt_count + 1,
            status=GoalAttemptStatus.QUEUED,
            turn_id=turn_id,
        )
        try:
            record = self.store.append_attempt(
                thread_id,
                attempt,
                expected_revision=expected_revision,
            )
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, record)
        return attempt

    def update_attempt(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        attempt_id: str,
        status: GoalAttemptStatus,
        turn_id: str | None = None,
    ) -> GoalAttempt:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        attempt = next(
            (item for item in current.attempts if item.id == attempt_id),
            None,
        )
        if attempt is None:
            raise ConflictError(f"Goal Attempt not found: {attempt_id}")
        binding_turn = (
            status is attempt.status and attempt.turn_id is None and turn_id is not None
        )
        if status is attempt.status and not binding_turn:
            if turn_id is None or turn_id == attempt.turn_id:
                return attempt
            raise ConflictError("Goal Attempt is already bound to another Turn")
        if not binding_turn and status not in _ATTEMPT_TRANSITIONS[attempt.status]:
            raise ConflictError(
                f"invalid Goal Attempt transition: {attempt.status} -> {status}"
            )
        now = utc_now()
        updated = replace(
            attempt,
            status=status,
            turn_id=turn_id or attempt.turn_id,
            updated_at=now,
            completed_at=now if status.is_terminal else None,
        )
        try:
            record = self.store.append_attempt(
                thread_id,
                updated,
                expected_revision=expected_revision,
            )
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, record)
        return updated

    def apply_evaluation(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        evaluation: GoalEvaluation,
    ) -> GoalRecord:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        if evaluation.goal_id != current.goal.id:
            raise ConflictError("evaluation belongs to another Goal")
        now = utc_now()
        elapsed = self._elapsed_at(current.goal, now)
        status = {
            GoalVerdict.COMPLETE: GoalStatus.COMPLETED,
            GoalVerdict.CONTINUE: GoalStatus.ACTIVE,
            GoalVerdict.BLOCKED: GoalStatus.BLOCKED,
            GoalVerdict.ERROR: GoalStatus.PAUSED,
        }[evaluation.verdict]
        completed_at = now if status is GoalStatus.COMPLETED else None
        active_since = now if status is GoalStatus.ACTIVE else None
        updated = replace(
            current.goal,
            status=status,
            revision=current.goal.revision + 1,
            tokens_used=current.goal.tokens_used + evaluation.tokens_used,
            elapsed_seconds=elapsed,
            last_verdict=evaluation.verdict,
            last_reason=evaluation.reason,
            updated_at=now,
            active_since=active_since,
            completed_at=completed_at,
        )
        if status is GoalStatus.ACTIVE and self._budget_exhausted(
            replace(current, goal=updated)
        ):
            updated = replace(
                updated,
                status=GoalStatus.BUDGET_LIMITED,
                active_since=None,
                last_reason="Goal execution budget was exhausted.",
            )
        try:
            record = self.store.commit_evaluation(
                thread_id,
                evaluation,
                updated,
                expected_revision=expected_revision,
            )
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, record)
        return record

    def _set_status(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        status: GoalStatus,
        reason: str,
    ) -> GoalRecord:
        current = self._required(thread_id)
        self._expect_revision(current, expected_revision)
        if current.goal.status is status:
            return current
        if current.goal.status.is_terminal:
            raise ConflictError("a terminal Goal cannot change status")
        now = utc_now()
        updated = replace(
            current.goal,
            status=status,
            revision=current.goal.revision + 1,
            elapsed_seconds=self._elapsed_at(current.goal, now),
            last_reason=reason.strip() or current.goal.last_reason,
            active_since=now if status is GoalStatus.ACTIVE else None,
            updated_at=now,
        )
        return self._update(updated, expected_revision=expected_revision)

    def _update(self, goal: Goal, *, expected_revision: int) -> GoalRecord:
        try:
            record = self.store.update(goal, expected_revision=expected_revision)
        except GoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(goal.thread_id, record)
        return record

    def _notify(self, thread_id: str, record: GoalRecord | None) -> None:
        if self._update_sink is None:
            return
        try:
            self._update_sink(thread_id, record)
        except Exception:  # noqa: BLE001 - ledger remains authoritative
            logger.exception("failed to publish Goal update for %s", thread_id)

    def _required(self, thread_id: str) -> GoalRecord:
        current = self.read(thread_id)
        if current is None:
            raise GoalNotFoundError(f"Goal not found for Session: {thread_id}")
        return current

    @staticmethod
    def _expect_revision(record: GoalRecord, expected_revision: int) -> None:
        if record.goal.revision != expected_revision:
            raise ConflictError(
                "Goal changed in another client; reload before retrying"
            )

    @staticmethod
    def _objective(value: str) -> str:
        clean = value.strip()
        if not clean:
            raise InvalidArgumentError("Goal objective must not be empty")
        if len(clean) > GOAL_OBJECTIVE_MAX_CHARS:
            raise InvalidArgumentError(
                "Goal objective may contain at most "
                f"{GOAL_OBJECTIVE_MAX_CHARS} characters"
            )
        return clean

    @staticmethod
    def _criteria(values: tuple[str, ...]) -> tuple[str, ...]:
        clean = tuple(value.strip() for value in values if value.strip())
        if len(clean) > GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS:
            raise InvalidArgumentError(
                "a Goal may contain at most "
                f"{GOAL_ACCEPTANCE_CRITERIA_MAX_ITEMS} acceptance criteria"
            )
        if any(len(value) > GOAL_ACCEPTANCE_CRITERION_MAX_CHARS for value in clean):
            raise InvalidArgumentError(
                "each acceptance criterion may contain at most "
                f"{GOAL_ACCEPTANCE_CRITERION_MAX_CHARS} characters"
            )
        return clean

    @staticmethod
    def _skills(values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            clean = tuple(SkillSelection(skill_id=value).skill_id for value in values)
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if len(clean) > MAX_SELECTED_SKILLS:
            raise InvalidArgumentError(
                f"a Goal may select at most {MAX_SELECTED_SKILLS} Skills"
            )
        if len(set(clean)) != len(clean):
            raise InvalidArgumentError("Goal Skill IDs must be unique")
        return clean

    def _load_session_policy(self, thread_id: str) -> GoalPolicyConfig:
        session = self.store.sessions.get_session(thread_id)
        if session is None:
            raise ThreadNotFoundError(f"session not found: {thread_id}")
        workspace = (session.metadata or {}).get("workspace")
        return load_config_for_workspace(
            Path(workspace) if isinstance(workspace, str) and workspace else Path.cwd()
        ).goal

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @staticmethod
    def _verification_timeout(value: int) -> int:
        if (
            isinstance(value, bool)
            or not 1 <= value <= GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS
        ):
            raise InvalidArgumentError(
                "verification timeout must be between 1 and "
                f"{GOAL_VERIFICATION_TIMEOUT_MAX_SECONDS} seconds"
            )
        return value

    @staticmethod
    def _elapsed_at(goal: Goal, now) -> int:
        if goal.active_since is None:
            return goal.elapsed_seconds
        delta = max(0, int((now - goal.active_since).total_seconds()))
        return goal.elapsed_seconds + delta

    @classmethod
    def _budget_exhausted(cls, record: GoalRecord) -> bool:
        budget = record.goal.budget
        elapsed = cls._elapsed_at(record.goal, utc_now())
        return bool(
            (
                budget.max_attempts is not None
                and record.attempt_count >= budget.max_attempts
            )
            or (
                budget.max_tokens is not None
                and record.goal.tokens_used >= budget.max_tokens
            )
            or (
                budget.max_elapsed_seconds is not None
                and elapsed >= budget.max_elapsed_seconds
            )
        )


__all__ = ["GoalService", "GoalUpdateSink", "PolicyLoader"]
