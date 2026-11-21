"""A rejection says whether evidence is *missing* or *contradicting*.

arXiv:2611.25760 ("Unfaithful Work Reports by Coding Agents") separates task
success from reporting faithfulness, and its strongest detector's one reported
false alarm came from collapsing them: a claim that a real side effect happened
was flagged only because no later observation confirmed it. "We did not see it"
is not "it did not happen".

``GoalVerdict.conflict`` carries that distinction. This file pins it so a later
refactor cannot quietly turn every rejection into a failure signal:

* a recorded *failure* of the claimed work is a conflict (``conflict=True``);
* a claim with nothing behind it is merely insufficient (``conflict=False``) --
  the model should produce evidence, not be told the work is wrong.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.goal_verifier import (
    GoalCompletionRequest,
    GoalEvidenceItem,
    GoalVerdict,
    RuleBasedVerifier,
    review_record,
)


def _request(
    *,
    evidence: tuple[GoalEvidenceItem, ...],
    reason: str = "The tests pass and the file is written.",
) -> GoalCompletionRequest:
    return GoalCompletionRequest(
        objective="Ship the feature.",
        reason=reason,
        evidence=evidence,
    )


@pytest.mark.asyncio
async def test_failed_check_is_a_conflict() -> None:
    verdict = await RuleBasedVerifier()(
        _request(
            evidence=(
                GoalEvidenceItem(
                    kind="command_execution",
                    status="failed",
                    summary="pytest -q exited 1",
                ),
            )
        )
    )
    assert verdict.confirmed is False
    assert verdict.conflict is True


@pytest.mark.asyncio
async def test_missing_evidence_is_insufficient_not_a_conflict() -> None:
    verdict = await RuleBasedVerifier()(_request(evidence=()))
    assert verdict.confirmed is False
    assert verdict.conflict is False


@pytest.mark.asyncio
async def test_no_reason_is_insufficient_not_a_conflict() -> None:
    verdict = await RuleBasedVerifier()(
        _request(
            evidence=(
                GoalEvidenceItem(
                    kind="file_edit", status="succeeded", summary="wrote app.py"
                ),
            ),
            reason="",
        )
    )
    assert verdict.confirmed is False
    assert verdict.conflict is False


@pytest.mark.asyncio
async def test_supported_claim_is_confirmed_without_conflict() -> None:
    verdict = await RuleBasedVerifier()(
        _request(
            evidence=(
                GoalEvidenceItem(
                    kind="command_execution",
                    status="succeeded",
                    summary="pytest -q exited 0",
                ),
            )
        )
    )
    assert verdict.confirmed is True
    assert verdict.conflict is False


# --- the report axis is recorded next to the outcome, never merged --------


@pytest.mark.asyncio
async def test_faithfulness_is_derived_for_all_three_states() -> None:
    """One property, three states, derived from the two booleans.

    Storing a third field would let it disagree with ``confirmed``/``conflict``;
    deriving it means a verdict can never claim to be faithful and contradicted
    at once.
    """
    supported = await RuleBasedVerifier()(
        _request(evidence=(GoalEvidenceItem("command_execution", "succeeded", "ok"),))
    )
    contradicted = await RuleBasedVerifier()(
        _request(evidence=(GoalEvidenceItem("command_execution", "failed", "boom"),))
    )
    unsupported = await RuleBasedVerifier()(_request(evidence=()))

    assert supported.faithfulness == "supported"
    assert contradicted.faithfulness == "contradicted"
    assert unsupported.faithfulness == "unsupported"


def test_review_record_keeps_faithfulness_beside_the_result() -> None:
    """The durable record carries the report axis, not just passed/rejected."""
    record = review_record(
        goal_id="goal_1",
        turn_id="turn_1",
        result="rejected",
        verdict=GoalVerdict(
            confirmed=False, justification="a record contradicts it", conflict=True
        ),
    )
    assert record["result"] == "rejected"
    assert record["faithfulness"] == "contradicted"


def test_review_record_without_a_verdict_says_unassessed() -> None:
    """An unavailable review assessed nothing -- say so instead of guessing."""
    record = review_record(
        goal_id="goal_1",
        turn_id="turn_1",
        result="unavailable",
        problem="the verifier could not run",
    )
    assert record["faithfulness"] == "unassessed"
