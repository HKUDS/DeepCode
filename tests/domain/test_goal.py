from __future__ import annotations

from dataclasses import replace

import pytest

from core.domain.common import utc_now
from core.domain.goal import (
    Goal,
    GoalAttempt,
    GoalAttemptStatus,
    GoalBudget,
    GoalEvaluation,
    GoalStatus,
    GoalVerdict,
)


def test_goal_requires_a_completed_timestamp_only_when_completed() -> None:
    goal = Goal(thread_id="session-1", objective="Make tests pass")

    with pytest.raises(ValueError, match="completed goals require"):
        replace(goal, status=GoalStatus.COMPLETED, active_since=None)

    completed = replace(
        goal,
        status=GoalStatus.COMPLETED,
        active_since=None,
        completed_at=utc_now(),
    )
    assert completed.status.is_terminal
    assert not completed.status.automatically_continues


def test_goal_budget_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        GoalBudget(max_attempts=0)


def test_goal_attempt_and_evaluation_validate_references() -> None:
    goal = Goal(thread_id="session-1", objective="Ship")
    attempt = GoalAttempt(
        goal_id=goal.id,
        goal_revision=goal.revision,
        ordinal=1,
        status=GoalAttemptStatus.COMPLETED,
        turn_id="turn_1",
        completed_at=utc_now(),
    )
    evaluation = GoalEvaluation(
        goal_id=goal.id,
        goal_revision=goal.revision,
        attempt_id=attempt.id,
        turn_id="turn_1",
        verdict=GoalVerdict.COMPLETE,
        reason="The recorded verification passed.",
        evidence_refs=("item_1",),
    )

    assert attempt.status.is_terminal
    assert evaluation.verdict is GoalVerdict.COMPLETE
