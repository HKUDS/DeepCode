"""The headless Goal runner reports a Goal together with its own outcome."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import cli.goal_runner as goal_runner
from cli.service_catalogs import ServiceGoals
from core.application.views import goal_outcome_view, thread_goal_view
from core.domain.thread_goal import (
    GoalDecisionSource,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)


def test_resuming_a_completed_goal_returns_its_own_outcome(monkeypatch) -> None:
    completed = ThreadGoal(
        id="goal_000000000000000000000001",
        thread_id="session-1",
        objective="Ship the parser",
        status=ThreadGoalStatus.COMPLETE,
    )
    outcome = GoalOutcome(
        status=ThreadGoalStatus.COMPLETE,
        reason="Shipped.",
        source=GoalDecisionSource.AGENT,
        decided_by_turn_id=None,
        decided_at=completed.updated_at,
    )
    # Another client reopens the Goal right after the runner first reads it.
    states = [
        {"goal": thread_goal_view(completed), "outcome": goal_outcome_view(outcome)},
        {
            "goal": thread_goal_view(
                ThreadGoal(
                    id=completed.id,
                    thread_id=completed.thread_id,
                    objective=completed.objective,
                    status=ThreadGoalStatus.ACTIVE,
                )
            ),
            "outcome": None,
        },
    ]

    class Rpc:
        def call(self, method, params):
            assert method == "thread/goal/get"
            return states.pop(0) if len(states) > 1 else states[0]

    class Client:
        def __init__(self, **_kwargs) -> None:
            self.thread = SimpleNamespace(id="session-1")
            self.workspace = "/ws"
            self.goals = ServiceGoals(Rpc())

        async def close(self) -> None:
            return None

    monkeypatch.setattr(goal_runner, "ServiceThreadClient", Client)

    result = asyncio.run(
        goal_runner.resume_goal(goal_runner.GoalResumeOptions(session_id="session-1"))
    )

    assert result.goal.status is ThreadGoalStatus.COMPLETE
    assert result.outcome is not None and result.outcome.reason == "Shipped."
