from __future__ import annotations

from datetime import timedelta

import pytest

from core.domain.common import utc_now
from core.domain.thread_goal import ThreadGoal, ThreadGoalStatus


def test_objective_edit_preserves_goal_identity_and_usage() -> None:
    created = utc_now()
    goal = ThreadGoal(
        thread_id="session-1",
        objective="Ship the first version",
        token_budget=500,
        tokens_used=120,
        time_used_seconds=9,
        created_at=created,
        updated_at=created,
    )

    edited = goal.edit(
        "Ship the corrected version",
        token_budget=700,
        skill_ids=("sk_0123456789abcdef01234567",),
        now=created + timedelta(seconds=1),
    )

    assert edited.id == goal.id
    assert edited.objective == "Ship the corrected version"
    assert edited.tokens_used == 120
    assert edited.time_used_seconds == 9
    assert edited.updated_at > goal.updated_at


def test_user_and_agent_transitions_have_separate_authority() -> None:
    goal = ThreadGoal(thread_id="session-1", objective="Ship")

    paused = goal.user_transition(ThreadGoalStatus.PAUSED)
    resumed = paused.user_transition(ThreadGoalStatus.ACTIVE)
    complete = resumed.agent_transition(ThreadGoalStatus.COMPLETE)

    assert paused.status is ThreadGoalStatus.PAUSED
    assert resumed.status is ThreadGoalStatus.ACTIVE
    assert complete.status is ThreadGoalStatus.COMPLETE
    assert (
        complete.user_transition(ThreadGoalStatus.ACTIVE).status
        is ThreadGoalStatus.ACTIVE
    )
    with pytest.raises(ValueError, match="Agent may only"):
        goal.agent_transition(ThreadGoalStatus.PAUSED)
    with pytest.raises(ValueError, match="invalid user"):
        goal.user_transition(ThreadGoalStatus.COMPLETE)


def test_usage_exhaustion_is_generic_budget_accounting() -> None:
    goal = ThreadGoal(
        thread_id="session-1",
        objective="Ship",
        token_budget=100,
        tokens_used=70,
    )

    limited = goal.add_usage(tokens=30, elapsed_seconds=4)

    assert limited.tokens_used == 100
    assert limited.time_used_seconds == 4
    assert limited.status is ThreadGoalStatus.BUDGET_LIMITED
    with pytest.raises(ValueError, match="increase or remove"):
        limited.user_transition(ThreadGoalStatus.ACTIVE)


def test_terminal_runtime_error_blocks_only_an_active_goal() -> None:
    active = ThreadGoal(thread_id="session-1", objective="Ship")
    paused = active.user_transition(ThreadGoalStatus.PAUSED)

    assert active.block_after_error().status is ThreadGoalStatus.BLOCKED
    assert paused.block_after_error() is paused


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"token_budget": 0}, "token_budget"),
        ({"tokens_used": -1}, "tokens_used"),
        ({"time_used_seconds": -1}, "time_used_seconds"),
        (
            {"skill_ids": ("not-a-skill",)},
            "opaque sk_",
        ),
    ],
)
def test_thread_goal_rejects_invalid_generic_state(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        ThreadGoal(thread_id="session-1", objective="Ship", **kwargs)
