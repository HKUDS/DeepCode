from __future__ import annotations

from core.application.errors import ConflictError
from core.application.goal_service import GoalService
from core.config import GoalPolicyConfig
from core.domain.goal import (
    GoalAttemptStatus,
    GoalBudget,
    GoalEvaluation,
    GoalStatus,
    GoalVerdict,
)
from core.sessions import GoalStore, SessionStore


def _service(tmp_path, *, max_attempts: int = 4):
    sessions = SessionStore(tmp_path / "sessions", use_index=False)
    session = sessions.create_session(
        title="Goal",
        metadata={"workspace": str(tmp_path)},
    )
    service = GoalService(
        GoalStore(sessions),
        policy_loader=lambda _: GoalPolicyConfig(
            max_attempts=max_attempts,
            max_elapsed_seconds=3600,
            stall_threshold=2,
        ),
    )
    return service, session.session_id


def test_goal_service_uses_configurable_policy_and_lifecycle(tmp_path) -> None:
    service, thread_id = _service(tmp_path)

    created = service.create(thread_id, objective="  Ship the feature  ")
    assert created.goal.objective == "Ship the feature"
    assert created.goal.budget.max_attempts == 4
    assert created.goal.status is GoalStatus.ACTIVE

    paused = service.pause(thread_id, expected_revision=1)
    assert paused.goal.status is GoalStatus.PAUSED
    assert paused.goal.active_since is None

    resumed = service.resume(thread_id, expected_revision=2)
    assert resumed.goal.status is GoalStatus.ACTIVE
    assert resumed.goal.revision == 3
    assert resumed.goal.active_since is not None

    service.clear(thread_id, expected_revision=3)
    assert service.read(thread_id) is None


def test_goal_edit_versions_the_definition_and_rejects_stale_clients(tmp_path) -> None:
    service, thread_id = _service(tmp_path)
    created = service.create(thread_id, objective="Initial")

    edited = service.edit(
        thread_id,
        expected_revision=created.goal.revision,
        objective="Updated",
        acceptance_criteria=("Tests pass",),
        budget=GoalBudget(max_attempts=2),
        skill_ids=(),
        verification_command_id=None,
        verification_timeout_seconds=300,
        evaluator_connection_id=None,
        evaluator_model_id=None,
    )

    assert edited.goal.revision == 2
    assert edited.goal.definition_revision == 2
    assert edited.goal.acceptance_criteria == ("Tests pass",)
    try:
        service.pause(thread_id, expected_revision=1)
    except ConflictError as exc:
        assert "reload" in str(exc)
    else:  # pragma: no cover - a stale write must never succeed
        raise AssertionError("stale Goal write unexpectedly succeeded")


def test_goal_attempt_and_evaluation_are_durable_and_versioned(tmp_path) -> None:
    service, thread_id = _service(tmp_path)
    goal = service.create(thread_id, objective="Make verification pass").goal
    attempt = service.begin_attempt(thread_id, expected_revision=goal.revision)
    attempt = service.update_attempt(
        thread_id,
        expected_revision=goal.revision,
        attempt_id=attempt.id,
        status=GoalAttemptStatus.QUEUED,
        turn_id="turn_1",
    )
    service.update_attempt(
        thread_id,
        expected_revision=goal.revision,
        attempt_id=attempt.id,
        status=GoalAttemptStatus.RUNNING,
    )
    service.update_attempt(
        thread_id,
        expected_revision=goal.revision,
        attempt_id=attempt.id,
        status=GoalAttemptStatus.EVALUATING,
    )
    evaluation = GoalEvaluation(
        goal_id=goal.id,
        goal_revision=goal.definition_revision,
        attempt_id=attempt.id,
        turn_id="turn_1",
        verdict=GoalVerdict.CONTINUE,
        reason="The required integration test is still missing.",
        tokens_used=11,
    )

    continued = service.apply_evaluation(
        thread_id,
        expected_revision=goal.revision,
        evaluation=evaluation,
    )
    service.update_attempt(
        thread_id,
        expected_revision=continued.goal.revision,
        attempt_id=attempt.id,
        status=GoalAttemptStatus.COMPLETED,
    )

    reloaded = service.read(thread_id)
    assert reloaded is not None
    assert reloaded.goal.status is GoalStatus.ACTIVE
    assert reloaded.goal.tokens_used == 11
    assert reloaded.attempt_count == 1
    assert reloaded.latest_evaluation == evaluation
    assert reloaded.latest_attempt is not None
    assert reloaded.latest_attempt.status is GoalAttemptStatus.COMPLETED


def test_goal_budget_stops_before_an_extra_attempt(tmp_path) -> None:
    service, thread_id = _service(tmp_path, max_attempts=1)
    goal = service.create(thread_id, objective="Bounded").goal
    service.begin_attempt(thread_id, expected_revision=goal.revision)

    try:
        service.begin_attempt(thread_id, expected_revision=goal.revision)
    except ConflictError as exc:
        assert "budget" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("Goal exceeded its configured Attempt budget")

    stopped = service.read(thread_id)
    assert stopped is not None
    assert stopped.goal.status is GoalStatus.BUDGET_LIMITED
