"""Goal completion verifier: single async gate, justification, blocked-after-N."""

from __future__ import annotations

import asyncio
import json

import pytest

from core.agent_runtime.goal_runtime import (
    GoalRuntimeContext,
    GoalRuntimeRouter,
)
from core.agent_runtime.goal_verifier import (
    GoalCompletionRequest,
    GoalEvidenceItem,
    GoalVerdict,
    RuleBasedVerifier,
)
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.tools.goal import UpdateGoalTool


class FakeGoalHandler:
    """Application-owned durable operations, recorded for assertions."""

    def __init__(self, objective: str = "Ship the feature.") -> None:
        self.requests: list[dict[str, object]] = []
        self.objective = objective
        self.events: list[str] = []

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
        self.events.append("completion_request")
        return GoalCompletionRequest(
            objective=self.objective,
            reason=reason or "",
            evidence=(
                GoalEvidenceItem(
                    kind="test_result",
                    status="completed",
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
            "reason": (
                "Blocked by the completion verifier after 2 rejected completion "
                "claims. Last rejection: No file change evidence."
            ),
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


@pytest.mark.asyncio
async def test_disarmed_router_records_completion_without_verifying() -> None:
    """configure_completion_verifier(None) restores the plain path."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)
    verifier = RejectingVerifier()
    runtime.configure_completion_verifier(verifier)
    runtime.configure_completion_verifier(None)
    runtime.activate(_context())

    result = await runtime.request(status="complete", reason="Shipped.")

    assert verifier.calls == 0
    assert handler.events == []
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_settle_runs_before_the_claim_is_built() -> None:
    """The Turn record catches up with reported steps before it is read."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    runtime.configure(handler)

    async def settle() -> None:
        handler.events.append("settle")

    runtime.configure_completion_verifier(ConfirmingVerifier(), settle=settle)
    runtime.activate(_context())

    await runtime.request(status="complete", reason="Shipped.")

    assert handler.events == ["settle", "completion_request"]


def _item(kind: str, status: str, summary: str, name: str = "") -> GoalEvidenceItem:
    return GoalEvidenceItem(kind=kind, status=status, summary=summary, name=name)


def _judge(
    *evidence: GoalEvidenceItem, pending=(), objective="Ship it.", reason="Done."
):
    return asyncio.run(
        RuleBasedVerifier()(
            GoalCompletionRequest(
                objective=objective,
                reason=reason,
                evidence=tuple(evidence),
                pending=tuple(pending),
            )
        )
    )


RED = _item("command_execution", "failed", "python -m pytest -q", "bash")
GREEN = _item("command_execution", "completed", "python -m pytest -q", "bash")
FIX = _item("file_change", "completed", "calc.py", "write")
BAD_READ = _item("tool_call", "failed", "calculator.py", "read")
READ = _item("tool_call", "completed", "calc.py", "read")


def test_rule_requires_an_objective_and_a_reason() -> None:
    assert "no objective" in _judge(READ, objective=" ").justification.lower()
    assert "no reason" in _judge(READ, reason=" ").justification.lower()


def test_rule_rejects_a_claim_with_no_finished_work() -> None:
    verdict = _judge()

    assert verdict.confirmed is False
    assert "no finished work" in verdict.justification


def test_rule_accepts_a_fixed_failure_red_fix_green() -> None:
    assert _judge(RED, FIX, GREEN).confirmed is True


def test_rule_accepts_an_early_failed_read_followed_by_work() -> None:
    assert _judge(BAD_READ, READ).confirmed is True


def test_rule_rejects_when_the_latest_check_failed_and_names_it() -> None:
    verdict = _judge(GREEN, FIX, RED, READ)

    assert verdict.confirmed is False
    assert "latest check failed: bash `python -m pytest -q`" in verdict.justification


def test_rule_rejects_when_the_latest_step_failed_and_names_it() -> None:
    verdict = _judge(GREEN, READ, BAD_READ)

    assert verdict.confirmed is False
    assert "latest step failed: read `calculator.py`" in verdict.justification


def test_rule_rejects_a_declined_check_without_asking_to_rerun_it() -> None:
    declined = _item("command_execution", "declined", "python -m pytest -q", "bash")
    verdict = _judge(GREEN, FIX, declined)

    assert verdict.confirmed is False
    assert "latest check was declined before it ran" in verdict.justification
    assert "run the check again" not in verdict.justification


def test_rule_never_decides_on_pending_calls_but_points_at_them() -> None:
    # The passing re-run went out in the same step as the claim.
    verdict = _judge(RED, FIX, pending=(GREEN,))

    assert verdict.confirmed is False
    assert "latest check failed" in verdict.justification
    assert "same step as this claim" in verdict.justification
    # A pending read cannot fix a failed check, so it is not offered as one.
    unrelated = _judge(RED, pending=(READ,))
    assert "same step" not in unrelated.justification
    # Pending calls never turn a supported claim into a rejection.
    assert _judge(GREEN, pending=(BAD_READ,)).confirmed is True


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


def test_configure_completion_verifier_rejects_non_positive_max() -> None:
    runtime = GoalRuntimeRouter()

    with pytest.raises(ValueError):
        runtime.configure_completion_verifier(ConfirmingVerifier(), max_rejections=0)


def test_max_rejections_below_one_is_a_config_error(tmp_path) -> None:
    from core.config import ConfigError, load_config

    path = tmp_path / "deepcode_config.json"
    path.write_text(
        json.dumps({"goal": {"completionVerifier": "rule", "maxRejections": 0}})
    )

    with pytest.raises(ConfigError, match="goal.maxRejections|goal.max_rejections"):
        load_config(path)


class _Profile:
    model = "fake-model"


class _IdleProvider:
    def get_default_model(self) -> str:
        return "fake-model"


@pytest.mark.asyncio
async def test_rule_setting_arms_the_router_and_off_disarms_it_on_rebuild(
    monkeypatch, tmp_path
) -> None:
    from core import agent_setup
    from core.config import GoalConfig

    def runtime_with(goal: GoalConfig):
        config = type("C", (), {"security": None, "goal": goal})()
        return type("R", (), {"config": config})()

    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (_IdleProvider(), _Profile()),
    )
    router = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    handler.completion_request = lambda context, *, reason: GoalCompletionRequest(  # type: ignore[method-assign]
        objective="Ship it.", reason=reason or "", evidence=()
    )
    router.configure(handler)
    router.activate(_context())

    def build(goal: GoalConfig) -> None:
        session, _, _ = agent_setup.build_agent_session(
            workspace=str(tmp_path / "w"),
            goal_runtime=router,
            runtime=runtime_with(goal),
        )
        sessions.append(session)

    sessions: list = []
    build(GoalConfig(completion_verifier="rule"))
    rejected = await router.request(status="complete", reason="Nothing done.")
    assert rejected["updated"] is False
    assert handler.requests == []

    build(GoalConfig(completion_verifier="off"))
    accepted = await router.request(status="complete", reason="Nothing done.")
    assert accepted["status"] == "complete"
    for session in sessions:
        await session.aclose()
