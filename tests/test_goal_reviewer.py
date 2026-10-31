"""The independent Goal reviewer: its tools, its rules and its verdicts."""

from __future__ import annotations

import asyncio

import pytest

from core.agent_runtime.goal_verifier import (
    GoalCompletionRequest,
    GoalEvidenceItem,
    GoalVerdict,
)
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
)
from core.harness.agents.goal_reviewer import (
    AgentReviewVerifier,
    ReviewFailed,
    review_task,
    reviewer_security_profile,
    verdict_from_result,
)
from core.harness.tools import default_coding_tools

WORK = GoalEvidenceItem(
    kind="command_execution", status="completed", summary="pytest -q", name="bash"
)


def _request(*evidence: GoalEvidenceItem) -> GoalCompletionRequest:
    return GoalCompletionRequest(
        objective="Fix the parser.", reason="Tests pass.", evidence=evidence
    )


@pytest.mark.parametrize("fenced", [True, False])
def test_read_only_tools_cannot_change_the_workspace(monkeypatch, tmp_path, fenced):
    monkeypatch.setattr("core.harness.sandbox.fences_writes", lambda: fenced)
    registry = default_coding_tools(str(tmp_path), read_only_workspace=True)
    names = {
        definition["function"]["name"] for definition in registry.get_definitions()
    }
    assert names == (
        {"read", "grep", "glob", "bash"} if fenced else {"read", "grep", "glob"}
    )


def test_reviewer_rules_never_ask_and_keep_the_users_rules():
    parent = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)
    profile = reviewer_security_profile(parent)
    assert profile.approval_policy is ApprovalPolicy.NEVER
    assert profile.command_sandbox is True
    assert profile.permission_rules == parent.permission_rules
    assert reviewer_security_profile(None).command_sandbox is True


def test_the_task_carries_goal_reason_and_evidence():
    pending = GoalEvidenceItem(
        kind="test_result", status="in_progress", summary="npm test", name="bash"
    )
    task = review_task(
        GoalCompletionRequest(
            objective="Fix the parser.",
            reason="Tests pass.",
            evidence=(WORK,),
            pending=(pending,),
        )
    )
    assert "Fix the parser." in task
    assert "Tests pass." in task
    assert "- [completed] bash: pytest -q" in task
    assert "no result yet" in task and "npm test" in task


def _req(text: str, met: bool) -> dict:
    return {"requirement": text, "met": met, "evidence": "read it"}


def test_verdicts_read_as_the_agent_needs_them():
    passed = verdict_from_result(
        {"verdict": "pass", "summary": "All good.", "requirements": [_req("CSV", True)]}
    )
    assert passed == GoalVerdict(
        confirmed=True,
        justification=(
            "An independent review passed the claim: All good. "
            "(1 requirement(s) checked.)"
        ),
    )
    failed = verdict_from_result(
        {
            "verdict": "fail",
            "summary": "Half done.",
            "requirements": [_req("CSV export", False)],
            "missing": ["CSV export", " "],
        }
    )
    assert not failed.confirmed and not failed.impossible
    assert failed.justification.endswith("Still missing:\n- CSV export")
    impossible = verdict_from_result(
        {
            "verdict": "impossible",
            "summary": "No key.",
            "requirements": [_req("API", False)],
        }
    )
    assert impossible.impossible and not impossible.confirmed
    with pytest.raises(ValueError, match="unknown verdict"):
        verdict_from_result({"verdict": "maybe", "summary": "?"})


def test_a_pass_with_an_unmet_requirement_is_a_fail():
    verdict = verdict_from_result(
        {
            "verdict": "pass",
            "summary": "Tests pass.",
            "requirements": [
                _req("add validates", True),
                _req("zero stock is removed", False),
            ],
        }
    )
    assert not verdict.confirmed
    assert verdict.justification.endswith("Still missing:\n- zero stock is removed")


def test_a_verdict_without_checked_requirements_is_refused():
    with pytest.raises(ValueError, match="no requirements"):
        verdict_from_result({"verdict": "pass", "summary": "Fine.", "requirements": []})


def test_a_confirmed_verdict_cannot_be_impossible():
    with pytest.raises(ValueError):
        GoalVerdict(confirmed=True, justification="x", impossible=True)


def test_the_rule_check_runs_first_and_spares_the_review():
    calls = []

    async def run(section, task, on_usage):
        calls.append(task)
        on_usage(120)
        return {"verdict": "pass", "summary": "ok", "requirements": [_req("x", True)]}

    verifier = AgentReviewVerifier(run)
    failed = GoalEvidenceItem(
        kind="command_execution", status="failed", summary="pytest -q", name="bash"
    )
    verdict = asyncio.run(verifier(_request(failed)))
    assert not verdict.confirmed and calls == []
    passed = asyncio.run(verifier(_request(WORK)))
    assert passed.confirmed and passed.tokens == 120
    assert len(calls) == 1


def test_a_slow_or_silent_review_fails_closed():
    async def slow(section, task, on_usage):
        on_usage(40)
        await asyncio.sleep(1)

    async def silent(section, task, on_usage):
        on_usage(7)
        return None

    with pytest.raises(ReviewFailed, match="longer than") as slow_failure:
        asyncio.run(AgentReviewVerifier(slow, timeout_seconds=0.01)(_request(WORK)))
    assert slow_failure.value.tokens == 40
    with pytest.raises(ReviewFailed, match="no verdict") as silent_failure:
        asyncio.run(AgentReviewVerifier(silent)(_request(WORK)))
    assert silent_failure.value.tokens == 7


def test_an_unknown_verdict_fails_closed_with_its_cost():
    async def odd(section, task, on_usage):
        on_usage(9)
        return {"verdict": "maybe", "summary": "?"}

    with pytest.raises(ReviewFailed, match="unknown verdict") as failure:
        asyncio.run(AgentReviewVerifier(odd)(_request(WORK)))
    assert failure.value.tokens == 9


def test_read_only_commands_get_no_network(monkeypatch, tmp_path):
    from core.harness.tools import shell

    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(shell, "build_exec_command", capture)
    asyncio.run(
        shell.BashTool(str(tmp_path), read_only_workspace=True).execute(command="true")
    )
    assert seen["read_only"] is True and seen["allow_network"] is False
    asyncio.run(shell.BashTool(str(tmp_path)).execute(command="true"))
    assert seen["read_only"] is False and seen["allow_network"] is True
