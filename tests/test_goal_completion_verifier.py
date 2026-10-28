"""Goal completion verifier: single async gate, justification, blocked-after-N."""

from __future__ import annotations

import asyncio

import pytest

from core.agent_runtime.goal_runtime import (
    GoalRuntimeContext,
    GoalRuntimeRouter,
)
from core.agent_runtime.goal_verifier import (
    GoalCompletionRequest,
    GoalCompletionVerifier,
    GoalEvidenceItem,
    GoalVerdict,
    RuleBasedVerifier,
    model_backed_verifier,
)
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.tools.goal import UpdateGoalTool


class FakeGoalHandler:
    """Application-owned durable operations, recorded for assertions."""

    def __init__(self, objective: str = "Ship the feature.") -> None:
        self.requests: list[dict[str, object]] = []
        self.objective = objective

    def read_goal(self, context: GoalRuntimeContext):
        return {"goalId": context.goal_id, "objective": self.objective}

    def update_goal(
        self,
        context: GoalRuntimeContext,
        *,
        status: str,
        reason: str | None,
    ):
        self.requests.append(
            {"turnId": context.turn_id, "status": status, "reason": reason}
        )
        return {"goalId": context.goal_id, "status": status}

    def completion_request(
        self,
        context: GoalRuntimeContext,
        *,
        reason: str | None,
    ) -> GoalCompletionRequest:
        return GoalCompletionRequest(
            objective=self.objective,
            reason=reason or "",
            evidence=(
                GoalEvidenceItem(
                    kind="test_result",
                    status="passed",
                    summary="Focused tests pass.",
                ),
            ),
        )


class RejectingVerifier:
    """Async verifier that always rejects; counts calls to prove single-gate."""

    def __init__(self, justification: str = "No file change evidence.") -> None:
        self.calls = 0
        self.justification = justification

    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        self.calls += 1
        return GoalVerdict(confirmed=False, justification=self.justification)


class ConfirmingVerifier:
    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        return GoalVerdict(confirmed=True, justification="Evidence supports it.")


class RaisingVerifier:
    def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        raise RuntimeError("verifier backend unavailable")


def _context() -> GoalRuntimeContext:
    return GoalRuntimeContext(
        thread_id="session",
        goal_id="goal_1",
        turn_id="turn_1",
    )


@pytest.mark.asyncio
async def test_update_goal_tool_rejection_returns_justification_and_records_nothing() -> (
    None
):
    """E2E through UpdateGoalTool: async verifier rejects, model sees why."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    verifier = RejectingVerifier(justification="No file change evidence.")
    runtime.configure_completion_verifier(verifier)
    runtime.activate(_context())
    registry = ToolRegistry()
    registry.register(UpdateGoalTool(runtime))

    result = await registry.execute(
        "update_goal",
        {"status": "complete", "reason": "The tests pass, so it is done."},
    )

    # The verifier ran exactly once — no sync re-check, no bypass.
    assert verifier.calls == 1
    # The justification travels back to the model.
    assert result["updated"] is False
    assert result["verdict"]["confirmed"] is False
    assert result["verdict"]["justification"] == "No file change evidence."
    assert result["rejections"] == 1
    # No durable completion was recorded; the goal stays open.
    assert handler.requests == []
    # The terminal gate did not freeze: the model may keep working.
    assert runtime.visible_tool_names(("read", "update_goal")) == (
        "read",
        "update_goal",
    )


@pytest.mark.asyncio
async def test_goal_blocks_after_max_rejections_instead_of_burning_turns() -> None:
    """N rejections move the goal to blocked and freeze the turn."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    verifier = RejectingVerifier()
    runtime.configure_completion_verifier(verifier, max_rejections=2)
    runtime.activate(_context())
    registry = ToolRegistry()
    registry.register(UpdateGoalTool(runtime))

    first = await registry.execute(
        "update_goal", {"status": "complete", "reason": "claim one"}
    )
    # First rejection is returned as data; nothing durable yet.
    assert first["updated"] is False
    assert first["rejections"] == 1
    assert handler.requests == []

    second = await registry.execute(
        "update_goal", {"status": "complete", "reason": "claim two"}
    )

    assert verifier.calls == 2
    # Second (max) rejection auto-blocks the goal and freezes the turn.
    assert second["autoBlocked"] is True
    assert second["verdict"]["justification"]
    assert handler.requests == [
        {
            "turnId": "turn_1",
            "status": "blocked",
            "reason": "No file change evidence.",
        }
    ]
    assert runtime.visible_tool_names(("read", "update_goal")) == ()


@pytest.mark.asyncio
async def test_confirming_verifier_records_completion_exactly_once() -> None:
    """A confirmed claim records completion; the verifier is not called twice."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    calls = {"n": 0}

    async def verifier(request: GoalCompletionRequest) -> GoalVerdict:
        calls["n"] += 1
        return GoalVerdict(confirmed=True, justification="ok")

    runtime.configure_completion_verifier(verifier)
    runtime.activate(_context())

    result = await runtime.request(
        status="complete", reason="Feature shipped with tests."
    )

    assert calls["n"] == 1
    assert result["status"] == "complete"
    assert handler.requests == [
        {
            "turnId": "turn_1",
            "status": "complete",
            "reason": "Feature shipped with tests.",
        }
    ]


@pytest.mark.asyncio
async def test_raising_verifier_fails_closed_and_reports_to_model() -> None:
    """A verifier that raises rejects (fail closed) and tells the model why."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    runtime.configure_completion_verifier(RaisingVerifier())
    runtime.activate(_context())

    result = await runtime.request(status="complete", reason="Done.")

    assert result["updated"] is False
    assert "verifier backend unavailable" in result["verdict"]["justification"]
    assert handler.requests == []


@pytest.mark.asyncio
async def test_blocked_status_is_not_verified() -> None:
    """Only complete claims are verified; blocked goes straight through."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    verifier = RejectingVerifier()
    runtime.configure_completion_verifier(verifier)
    runtime.activate(_context())

    result = await runtime.request(status="blocked", reason="Upstream API is down.")

    assert verifier.calls == 0
    assert result["status"] == "blocked"
    assert handler.requests == [
        {"turnId": "turn_1", "status": "blocked", "reason": "Upstream API is down."}
    ]


def test_rule_based_verifier_requires_objective_reason_and_evidence() -> None:
    verifier = RuleBasedVerifier()

    no_objective = asyncio.run(
        verifier(GoalCompletionRequest(objective="  ", reason="done", evidence=()))
    )
    assert no_objective.confirmed is False
    assert "no objective" in no_objective.justification.lower()

    no_reason = asyncio.run(
        verifier(GoalCompletionRequest(objective="Ship it.", reason="   ", evidence=()))
    )
    assert no_reason.confirmed is False
    assert "no reason" in no_reason.justification.lower()

    no_evidence = asyncio.run(
        verifier(
            GoalCompletionRequest(
                objective="Ship it.", reason="Looks done.", evidence=()
            )
        )
    )
    assert no_evidence.confirmed is False
    assert "no evidence" in no_evidence.justification.lower()

    failed = asyncio.run(
        verifier(
            GoalCompletionRequest(
                objective="Ship it.",
                reason="Ran the suite.",
                evidence=(
                    GoalEvidenceItem(
                        kind="test_result",
                        status="failed",
                        summary="2 tests failed.",
                    ),
                ),
            )
        )
    )
    assert failed.confirmed is False
    assert "failed" in failed.justification.lower()

    good = asyncio.run(
        verifier(
            GoalCompletionRequest(
                objective="Ship it.",
                reason="Ran the suite.",
                evidence=(
                    GoalEvidenceItem(
                        kind="test_result",
                        status="passed",
                        summary="All tests pass.",
                    ),
                ),
            )
        )
    )
    assert good.confirmed is True


@pytest.mark.asyncio
async def test_model_backed_verifier_fail_closed_on_ask_error() -> None:
    async def ask(request: GoalCompletionRequest) -> str:
        raise ConnectionError("model unreachable")

    verifier = model_backed_verifier(ask)
    verdict = await verifier(
        GoalCompletionRequest(objective="Ship it.", reason="done", evidence=())
    )

    assert verdict.confirmed is False
    assert "model unreachable" in verdict.justification


@pytest.mark.asyncio
async def test_model_backed_verifier_treats_non_yes_as_rejection() -> None:
    async def ask(request: GoalCompletionRequest) -> str:
        return "no, the tests did not run"

    verifier = model_backed_verifier(ask)
    verdict = await verifier(
        GoalCompletionRequest(objective="Ship it.", reason="done", evidence=())
    )

    assert verdict.confirmed is False


@pytest.mark.asyncio
async def test_verifier_protocol_accepts_plain_async_callable() -> None:
    """A bare async function satisfies the verifier contract (no class needed)."""

    async def verifier(request: GoalCompletionRequest) -> GoalVerdict:
        return GoalVerdict(confirmed=True, justification="ok")

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    runtime.configure_completion_verifier(verifier)  # type: ignore[arg-type]
    runtime.activate(_context())

    result = await runtime.request(status="complete", reason="Shipped.")

    assert result["status"] == "complete"


def test_completion_request_carries_real_objective_and_evidence() -> None:
    """The request handed to the verifier is built by the handler, not hardcoded."""

    handler = FakeGoalHandler(objective="Fix the off-by-one in pagination.")
    request = handler.completion_request(_context(), reason="Tests pass now.")

    assert request.objective == "Fix the off-by-one in pagination."
    assert request.reason == "Tests pass now."
    assert request.evidence and request.evidence[0].kind == "test_result"


def test_configure_completion_verifier_rejects_non_positive_max() -> None:
    runtime = GoalRuntimeRouter()

    with pytest.raises(ValueError):
        runtime.configure_completion_verifier(ConfirmingVerifier(), max_rejections=0)


def test_verifier_contract_is_async_callable_protocol() -> None:
    """RuleBasedVerifier and model_backed_verifier both satisfy the Protocol."""

    async def _accept(verifier: GoalCompletionVerifier) -> None:
        await verifier(GoalCompletionRequest(objective="x", reason="y", evidence=()))

    asyncio.run(_accept(RuleBasedVerifier()))
    asyncio.run(_accept(model_backed_verifier(_yes)))


async def _yes(_request: GoalCompletionRequest) -> str:
    return "yes"
