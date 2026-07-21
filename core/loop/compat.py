"""Read-only compatibility projection from canonical Goal state to LoopState."""

from __future__ import annotations

import hashlib

from core.domain.goal import GoalRecord, GoalStatus, GoalVerdict
from core.loop.state import (
    STATUS_ERROR,
    STATUS_EXHAUSTED,
    STATUS_STALLED,
    STATUS_SUCCEEDED,
    LoopState,
    RoundRecord,
)


def project_goal_to_loop_state(
    record: GoalRecord,
    *,
    workspace: str,
    test_command: str,
    max_rounds: int,
) -> LoopState:
    """Keep the old state.json inspectable without making it authoritative."""

    evaluations = {item.attempt_id: item for item in record.evaluations}
    rounds = []
    for attempt in record.attempts:
        evaluation = evaluations.get(attempt.id)
        reason = evaluation.reason if evaluation is not None else attempt.status.value
        passed = (
            evaluation.verdict is GoalVerdict.COMPLETE
            if evaluation is not None and test_command
            else None
        )
        signature = (
            ""
            if passed is not False
            else hashlib.sha256(reason.casefold().encode()).hexdigest()[:16]
        )
        rounds.append(
            RoundRecord(
                index=attempt.ordinal - 1,
                agent_stop_reason=attempt.status.value,
                tests_passed=passed,
                test_summary=reason,
                test_signature=signature,
                handoff=reason[:2_400],
            )
        )
    state = LoopState(
        goal=record.goal.objective,
        workspace=workspace,
        test_command=test_command,
        max_rounds=max_rounds,
        rounds=rounds,
    )
    status = {
        GoalStatus.COMPLETED: STATUS_SUCCEEDED,
        GoalStatus.BUDGET_LIMITED: STATUS_EXHAUSTED,
        GoalStatus.BLOCKED: STATUS_STALLED,
        GoalStatus.PAUSED: STATUS_ERROR,
        GoalStatus.USAGE_LIMITED: STATUS_EXHAUSTED,
        GoalStatus.ACTIVE: STATUS_ERROR,
    }[record.goal.status]
    state.finish(status, record.goal.last_reason or record.goal.status.value)
    return state


__all__ = ["project_goal_to_loop_state"]
