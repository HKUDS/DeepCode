from __future__ import annotations

from dataclasses import replace

import pytest

from core.domain.common import utc_now
from core.domain.goal import (
    Goal,
    GoalAttempt,
    GoalAttemptStatus,
    GoalEvaluation,
    GoalStatus,
    GoalVerdict,
)
from core.sessions import GoalStore, SessionStore
from core.sessions.goal_store import (
    GoalConflictError,
    GoalLedgerCorruptError,
    GoalSessionNotFoundError,
)


def _store(tmp_path) -> tuple[SessionStore, GoalStore, str]:
    sessions = SessionStore(tmp_path / "sessions", use_index=False)
    session = sessions.create_session(title="Goal test")
    return sessions, GoalStore(sessions), session.session_id


def test_goal_store_round_trips_snapshots_attempts_and_evaluations(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = Goal(thread_id=session_id, objective="Make the verifier pass")
    created = goals.create(goal)
    attempt = GoalAttempt(
        goal_id=goal.id,
        goal_revision=goal.definition_revision,
        ordinal=1,
        status=GoalAttemptStatus.COMPLETED,
        turn_id="turn_1",
        completed_at=utc_now(),
    )
    goals.append_attempt(session_id, attempt, expected_revision=1)
    evaluation = GoalEvaluation(
        goal_id=goal.id,
        goal_revision=goal.definition_revision,
        attempt_id=attempt.id,
        turn_id="turn_1",
        verdict=GoalVerdict.CONTINUE,
        reason="One acceptance criterion is still missing.",
        evidence_refs=("item_test",),
    )
    goals.append_evaluation(session_id, evaluation, expected_revision=1)
    updated = replace(
        goal,
        status=GoalStatus.PAUSED,
        revision=2,
        active_since=None,
        last_verdict=GoalVerdict.CONTINUE,
        last_reason=evaluation.reason,
        updated_at=utc_now(),
    )
    goals.update(updated, expected_revision=1)

    reloaded = GoalStore(SessionStore(sessions.root, use_index=False)).read(session_id)

    assert created.goal == goal
    assert reloaded is not None
    assert reloaded.goal == updated
    assert reloaded.attempts == (attempt,)
    assert reloaded.evaluations == (evaluation,)


def test_goal_store_compare_and_swap_rejects_stale_writers(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = Goal(thread_id=session_id, objective="Ship")
    goals.create(goal)
    goals.update(
        replace(
            goal,
            revision=2,
            status=GoalStatus.PAUSED,
            active_since=None,
            updated_at=utc_now(),
        ),
        expected_revision=1,
    )

    with pytest.raises(GoalConflictError, match="revision changed"):
        goals.update(
            replace(
                goal,
                revision=2,
                status=GoalStatus.BLOCKED,
                active_since=None,
                updated_at=utc_now(),
            ),
            expected_revision=1,
        )


def test_goal_store_clear_allows_a_new_goal(tmp_path) -> None:
    _, goals, session_id = _store(tmp_path)
    first = Goal(thread_id=session_id, objective="First")
    goals.create(first)
    goals.clear(session_id, goal_id=first.id, expected_revision=1)

    assert goals.read(session_id) is None
    second = Goal(thread_id=session_id, objective="Second")
    assert goals.create(second).goal == second


def test_goal_store_requires_a_real_session(tmp_path) -> None:
    goals = GoalStore(SessionStore(tmp_path / "sessions", use_index=False))

    with pytest.raises(GoalSessionNotFoundError):
        goals.create(Goal(thread_id="missing", objective="No session"))


def test_goal_store_fails_closed_on_a_corrupt_ledger(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    directory = sessions.root / session_id
    (directory / "goal.jsonl").write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(GoalLedgerCorruptError):
        goals.read(session_id)
