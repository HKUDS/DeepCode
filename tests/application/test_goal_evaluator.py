from __future__ import annotations

import asyncio

from core.application.goal_evaluator import (
    GoalEvaluationContext,
    GoalEvaluator,
    SemanticDecision,
)
from core.application.test_service import (
    TestCommand as VerificationCommand,
    TestRunResult as VerificationRunResult,
)
from core.application.turn_service import TurnSnapshot
from core.domain.common import utc_now
from core.domain.goal import (
    Goal,
    GoalAttempt,
    GoalAttemptStatus,
    GoalRecord,
    GoalVerdict,
)
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.turn import Turn, TurnStatus


class FakeTests:
    def __init__(self, result: VerificationRunResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, *_args, **_kwargs) -> VerificationRunResult:
        self.calls += 1
        return self.result


class FakeSemantic:
    def __init__(self, decision: SemanticDecision) -> None:
        self.decision = decision
        self.calls = 0

    async def evaluate(self, _context) -> SemanticDecision:
        self.calls += 1
        return self.decision


def _context(*, verification_command_id: str | None):
    goal = Goal(
        thread_id="session",
        objective="Ship",
        verification_command_id=verification_command_id,
    )
    turn = Turn(
        id="turn_1",
        thread_id="session",
        ordinal=1,
        prompt="Ship",
        status=TurnStatus.COMPLETED,
        stop_reason="completed",
        completed_at=utc_now(),
    )
    attempt = GoalAttempt(
        goal_id=goal.id,
        goal_revision=goal.definition_revision,
        ordinal=1,
        status=GoalAttemptStatus.EVALUATING,
        turn_id=turn.id,
    )
    assistant = Item(
        thread_id="session",
        turn_id=turn.id,
        ordinal=1,
        kind=ItemKind.ASSISTANT_MESSAGE,
        status=ItemStatus.COMPLETED,
        summary="Done",
        payload={"text": "Done"},
    )
    return GoalEvaluationContext(
        record=GoalRecord(goal, attempts=(attempt,)),
        attempt=attempt,
        turn=TurnSnapshot(turn, (assistant,), ()),
        workspace="/workspace",
    )


def _test_result(*, passed: bool) -> VerificationRunResult:
    item = Item(
        thread_id="session",
        turn_id="turn_1",
        ordinal=2,
        kind=ItemKind.TEST_RESULT,
        status=ItemStatus.COMPLETED if passed else ItemStatus.FAILED,
        summary="Tests passed" if passed else "Tests failed",
    )
    return VerificationRunResult(
        item=item,
        command=VerificationCommand(
            "pytest",
            "Python tests",
            ("python", "-m", "pytest"),
        ),
        exit_code=0 if passed else 1,
        timed_out=False,
        duration_ms=1,
        stdout="ok" if passed else "",
        stderr="" if passed else "failed",
        output_truncated=False,
    )


def test_failed_deterministic_verification_cannot_be_overruled() -> None:
    semantic = FakeSemantic(
        SemanticDecision(
            GoalVerdict.COMPLETE,
            "Looks complete.",
            (),
            "fake",
            "fake-model",
            3,
        )
    )
    evaluator = GoalEvaluator(FakeTests(_test_result(passed=False)), semantic)

    result = asyncio.run(evaluator.evaluate(_context(verification_command_id="pytest")))

    assert result.verdict is GoalVerdict.CONTINUE
    assert semantic.calls == 0
    assert result.evidence_refs


def test_passing_verification_is_preserved_as_semantic_evidence() -> None:
    semantic = FakeSemantic(
        SemanticDecision(
            GoalVerdict.COMPLETE,
            "All criteria and verification passed.",
            (),
            "fake",
            "fake-model",
            9,
        )
    )
    tests = FakeTests(_test_result(passed=True))
    evaluator = GoalEvaluator(tests, semantic)

    result = asyncio.run(evaluator.evaluate(_context(verification_command_id="pytest")))

    assert result.verdict is GoalVerdict.COMPLETE
    assert result.evaluator_model == "fake-model"
    assert result.tokens_used == 9
    assert result.evidence_refs == (tests.result.item.id,)


def test_semantic_only_goal_does_not_run_a_test_command() -> None:
    semantic = FakeSemantic(
        SemanticDecision(
            GoalVerdict.CONTINUE,
            "The requested evidence is missing.",
            (),
            "fake",
            "fake-model",
            4,
        )
    )
    tests = FakeTests(_test_result(passed=True))
    evaluator = GoalEvaluator(tests, semantic)

    result = asyncio.run(evaluator.evaluate(_context(verification_command_id=None)))

    assert result.verdict is GoalVerdict.CONTINUE
    assert tests.calls == 0
