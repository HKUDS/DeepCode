"""Goal completion verifier: single async gate, justification, blocked-after-N."""

from __future__ import annotations

import asyncio
import dataclasses
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
        self.charged: list[int] = []
        self.turns = 3
        self.declined = False

    def goal_turn_count(self, context: GoalRuntimeContext) -> int:
        return self.turns

    def user_declined_in_turn(self, context: GoalRuntimeContext) -> bool:
        return self.declined

    def read_goal(self, context: GoalRuntimeContext):
        return {"goalId": context.goal_id, "objective": self.objective}

    def update_goal(
        self,
        context: GoalRuntimeContext,
        *,
        status: str,
        reason: str | None,
        expected_objective: str | None = None,
    ):
        if expected_objective is not None and expected_objective != self.objective:
            raise ValueError("objective changed")
        self.requests.append(
            {"turnId": context.turn_id, "status": status, "reason": reason}
        )
        return {"goalId": context.goal_id, "status": status}

    def record_verification_tokens(self, context: GoalRuntimeContext, tokens: int):
        self.charged.append(tokens)

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
                "claims with no new changes in between. Last rejection: No file "
                "change evidence."
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
@pytest.mark.parametrize("turns", [1, 2])
async def test_blocked_before_the_third_turn_is_not_recorded(turns: int) -> None:
    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    handler.turns = turns
    runtime.configure(handler)
    runtime.activate(_context())

    result = await runtime.request(status="blocked", reason="Upstream API is down.")

    assert result["updated"] is False
    assert f"Turn {turns} of the Goal" in result["note"]
    assert handler.requests == []
    # Not a terminal request: the model keeps its tools and goes on.
    assert runtime.visible_tool_names(("bash", "update_goal")) == (
        "bash",
        "update_goal",
    )


@pytest.mark.asyncio
async def test_blocked_is_recorded_at_once_when_the_user_declined() -> None:
    """Asking again for an approval the user just declined would be worse."""

    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler()
    handler.turns = 1
    handler.declined = True
    runtime.configure(handler)
    runtime.activate(_context())

    result = await runtime.request(status="blocked", reason="approval denied")

    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_blocked_is_recorded_when_the_turns_cannot_be_counted() -> None:
    class Uncountable(FakeGoalHandler):
        def goal_turn_count(self, context):
            raise RuntimeError("store unavailable")

    runtime = GoalRuntimeRouter()
    handler = Uncountable()
    runtime.configure(handler)
    runtime.activate(_context())

    result = await runtime.request(status="blocked", reason="Upstream API is down.")

    assert result["status"] == "blocked"


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

    # A declined step that is no check reads the same way.
    step = _judge(GREEN, _item("tool_call", "declined", "notes.txt", "write"))
    assert "latest step was declined before it ran" in step.justification


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


@pytest.mark.asyncio
async def test_a_reviews_tokens_are_charged_whatever_the_verdict() -> None:
    from core.harness.agents.goal_reviewer import ReviewFailed

    class Costly:
        def __init__(self, outcome):
            self.outcome = outcome

        async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    for outcome, charged in (
        (GoalVerdict(confirmed=True, justification="ok", tokens=300), [300]),
        (GoalVerdict(confirmed=False, justification="no", tokens=200), [200]),
        (ReviewFailed("provider down", tokens=50), [50]),
        (GoalVerdict(confirmed=True, justification="rule only"), []),
    ):
        runtime = GoalRuntimeRouter()
        handler = FakeGoalHandler()
        runtime.configure(handler)
        runtime.configure_completion_verifier(Costly(outcome))
        runtime.activate(_context())
        await runtime.request(status="complete", reason="Done.")
        assert handler.charged == charged, outcome


@pytest.mark.asyncio
async def test_an_objective_edited_during_the_review_is_not_marked_complete() -> None:
    runtime = GoalRuntimeRouter()
    handler = FakeGoalHandler(objective="Ship the feature.")

    class EditDuringReview:
        async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
            handler.objective = "Ship the feature and its docs."
            return GoalVerdict(confirmed=True, justification="ok", tokens=10)

    runtime.configure(handler)
    runtime.configure_completion_verifier(EditDuringReview())
    runtime.activate(_context())

    with pytest.raises(ValueError, match="objective changed"):
        await runtime.request(status="complete", reason="Done.")
    assert handler.requests == []
    assert handler.charged == [10]  # the review still cost what it cost


class _Verdict:
    """A verifier returning a fixed verdict, optionally editing the Goal first."""

    def __init__(self, verdict: GoalVerdict, before=None) -> None:
        self.verdict = verdict
        self.before = before

    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        if self.before is not None:
            self.before()
        return self.verdict


def _armed(handler, verifier, **kwargs) -> GoalRuntimeRouter:
    runtime = GoalRuntimeRouter()
    runtime.configure(handler)
    runtime.configure_completion_verifier(verifier, **kwargs)
    runtime.activate(_context())
    return runtime


@pytest.mark.asyncio
async def test_a_stale_impossible_verdict_does_not_block_an_edited_goal() -> None:
    handler = FakeGoalHandler(objective="Ship the feature.")
    edit = lambda: setattr(handler, "objective", "Ship something else.")  # noqa: E731
    runtime = _armed(
        handler,
        _Verdict(
            GoalVerdict(confirmed=False, justification="no", impossible=True), edit
        ),
    )

    with pytest.raises(ValueError, match="objective changed"):
        await runtime.request(status="complete", reason="Done.")
    assert handler.requests == []


@pytest.mark.asyncio
async def test_a_rejection_that_ends_the_goal_says_so_and_blocks_nothing() -> None:
    class BudgetHandler(FakeGoalHandler):
        active = True

        def read_goal(self, context):
            return {**super().read_goal(context), "canUpdate": self.active}

        def record_verification_tokens(self, context, tokens):
            super().record_verification_tokens(context, tokens)
            self.active = False  # the review used up the budget

    handler = BudgetHandler()
    runtime = _armed(
        handler,
        _Verdict(GoalVerdict(confirmed=False, justification="no", tokens=99)),
        max_rejections=1,
    )

    result = await runtime.request(status="complete", reason="Done.")
    assert result["updated"] is False and "autoBlocked" not in result
    assert (
        "cannot be claimed complete in this Turn" in result["verdict"]["justification"]
    )
    assert handler.requests == [] and handler.charged == [99]


@pytest.mark.asyncio
async def test_a_failed_charge_never_hides_the_decision() -> None:
    class Broken(FakeGoalHandler):
        def record_verification_tokens(self, context, tokens):
            raise OSError("disk full")

    handler = Broken()
    runtime = _armed(
        handler, _Verdict(GoalVerdict(confirmed=True, justification="ok", tokens=5))
    )
    result = await runtime.request(status="complete", reason="Done.")
    assert result["verdict"]["confirmed"] is True
    assert [r["status"] for r in handler.requests] == ["complete"]
    assert runtime.visible_tool_names(("update_goal",)) == ()


@pytest.mark.asyncio
async def test_a_stopped_review_still_charges_what_it_spent() -> None:
    from core.harness.agents.goal_reviewer import AgentReviewVerifier

    started = asyncio.Event()

    async def run(section, task, on_usage):
        on_usage(70)
        started.set()
        await asyncio.sleep(30)

    handler = FakeGoalHandler()
    runtime = _armed(handler, AgentReviewVerifier(run))
    task = asyncio.create_task(runtime.request(status="complete", reason="Done."))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handler.charged == [70] and handler.requests == []


@pytest.mark.asyncio
async def test_an_unverifiable_claim_blocks_the_goal_without_counting() -> None:
    from core.agent_runtime.goal_verifier import VerificationUnavailable

    class Down:
        async def __call__(self, request):
            raise VerificationUnavailable("provider down", tokens=12)

    handler = FakeGoalHandler()
    runtime = _armed(handler, Down())
    result = await runtime.request(status="complete", reason="Done.")
    assert result["unverified"] is True and "rejections" not in result
    assert "could not be verified (provider down)" in result["verdict"]["justification"]
    assert [r["status"] for r in handler.requests] == ["blocked"]
    assert "Resume the Goal to try again" in handler.requests[0]["reason"]
    assert handler.charged == [12]
    assert runtime.visible_tool_names(("update_goal",)) == ()


@pytest.mark.asyncio
async def test_an_unverifiable_claim_on_an_ended_goal_records_nothing() -> None:
    from core.agent_runtime.goal_verifier import VerificationUnavailable

    class Down:
        async def __call__(self, request):
            raise VerificationUnavailable("provider down", tokens=12)

    class Ended(FakeGoalHandler):
        def read_goal(self, context):
            return {**super().read_goal(context), "canUpdate": False}

    handler = Ended()
    runtime = _armed(handler, Down())
    result = await runtime.request(status="complete", reason="Done.")
    assert result["updated"] is False
    assert "no longer active" in result["verdict"]["justification"]
    assert handler.requests == []


class _Marked(FakeGoalHandler):
    """A handler whose claims carry a progress marker the test moves."""

    marker = "item_a"

    def completion_request(self, context, *, reason):
        request = super().completion_request(context, reason=reason)
        return dataclasses.replace(request, progress_marker=self.marker)


@pytest.mark.asyncio
async def test_rejections_count_only_while_nothing_changes() -> None:
    handler = _Marked()
    runtime = _armed(handler, RejectingVerifier(), max_rejections=2)

    first = await runtime.request(status="complete", reason="Done.")
    handler.marker = "item_b"  # the agent changed files
    second = await runtime.request(status="complete", reason="Done.")
    third = await runtime.request(status="complete", reason="Done.")  # no change

    assert [r["rejections"] for r in (first, second, third)] == [1, 1, 2]
    assert third["autoBlocked"] is True
    assert "with no new changes in between" in handler.requests[-1]["reason"]


@pytest.mark.asyncio
async def test_progress_never_buys_endless_claims() -> None:
    from core.agent_runtime.goal_runtime import MAX_TOTAL_REJECTIONS

    handler = _Marked()
    runtime = _armed(handler, RejectingVerifier(), max_rejections=3)
    for n in range(MAX_TOTAL_REJECTIONS):
        handler.marker = f"item_{n}"
        result = await runtime.request(status="complete", reason="Done.")
    assert result["autoBlocked"] is True
    assert result["totalRejections"] == MAX_TOTAL_REJECTIONS
    assert handler.requests[-1]["reason"].startswith(
        f"Blocked by the completion verifier after {MAX_TOTAL_REJECTIONS} rejected"
    )


@pytest.mark.asyncio
async def test_a_claim_without_any_change_is_no_progress() -> None:
    handler = _Marked()
    handler.marker = "item_a"
    runtime = _armed(handler, RejectingVerifier(), max_rejections=3)
    await runtime.request(status="complete", reason="Done.")
    handler.marker = ""  # a later Turn that changed nothing
    second = await runtime.request(status="complete", reason="Done.")
    handler.marker = "item_a"  # the same old change is no news either
    third = await runtime.request(status="complete", reason="Done.")
    assert [second["rejections"], third["rejections"]] == [2, 3]
    assert third["autoBlocked"] is True
