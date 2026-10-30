"""Independent verification of a Goal completion claim.

The verifier answers one question: does this Turn's evidence support the
model's claim that the Goal is complete? It is consulted exactly once per
completion request, asynchronously, inside the goal runtime — there is no
synchronous side door and no caller-supplied "already verified" flag.

Rejections are data, not exceptions. A rejected claim returns its
justification to the model so the model can either produce the missing
evidence or declare the Goal blocked; a verifier that raises fails closed
(rejects). After a configured number of rejections the goal runtime moves the
Goal to ``blocked`` rather than letting the model burn Turns retrying the same
claim.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GoalEvidenceItem:
    """One Turn evidence item, reduced to what a verifier judges."""

    kind: str
    status: str
    summary: str
    # The tool that produced the item (``read``, ``bash``...), when known.
    name: str = ""


@dataclass(frozen=True, slots=True)
class GoalCompletionRequest:
    """The claim under review: the real objective plus Turn evidence.

    ``evidence`` is this Turn's most recent *settled* evidence (a recorded
    result, ``completed`` or ``failed``), oldest first, without the Goal
    tools' own calls. ``pending`` holds calls made in the same model step as
    the claim: their results are only recorded once that step ends, so a
    verifier cannot know them yet.
    """

    objective: str
    reason: str
    evidence: tuple[GoalEvidenceItem, ...]
    pending: tuple[GoalEvidenceItem, ...] = ()


@dataclass(frozen=True, slots=True)
class GoalVerdict:
    """The verifier's answer. ``justification`` always travels to the model."""

    confirmed: bool
    justification: str


class GoalCompletionVerifier(Protocol):
    """An async callable that judges one completion claim."""

    def __call__(self, request: GoalCompletionRequest) -> Awaitable[GoalVerdict]: ...


_CHECK_KINDS = frozenset({"command_execution", "test_result"})
_UNSUCCESSFUL = frozenset({"failed", "declined"})


def _describe(item: GoalEvidenceItem) -> str:
    label = item.name or item.kind.replace("_", " ")
    return f"{label} `{item.summary}`" if item.summary else label


def _unsuccessful(what: str, item: GoalEvidenceItem, remedy: str) -> str:
    """Why the latest ``what`` does not support the claim.

    A declined step was refused before it ran: running it again would be
    refused the same way, so the advice differs from a failure's.
    """
    if item.status == "declined":
        return (
            f"The latest {what} was declined before it ran: {_describe(item)}. "
            "Verify the work another way before claiming completion."
        )
    return f"The latest {what} failed: {_describe(item)}. {remedy} before claiming completion."


class RuleBasedVerifier:
    """Deterministic check that a completion claim rests on finished work.

    This is the built-in verifier behind the ``rule`` setting. It judges
    whether the claim is *supported*, not whether the work is *good*. Over
    ``request.evidence`` (settled, newest last) a claim is rejected when:

    1. the Goal has no objective, or the claim gives no reason;
    2. there is no settled evidence — nothing in this Turn but Goal
       bookkeeping (``get_goal`` alone is not work);
    3. the latest check (a command run or test result) failed or was
       declined; or
    4. the latest evidence item of any kind failed or was declined.

    Only the latest outcome counts: a failure that later work moved past is
    resolved, so the usual red -> fix -> green loop, or a mistyped path early
    in the Turn, never blocks completion. Known limit: a command that exits
    non-zero by design (``grep`` with no match) reads as a failed check when it
    is the latest one; the justification names it, so the model can re-run a
    real check.

    ``request.pending`` never decides the verdict. When a pending call from the
    claim's own step could still overturn a rejection, the justification says
    so, so the model reads that result before claiming again.
    """

    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        if not request.objective.strip():
            return GoalVerdict(
                confirmed=False,
                justification="The Goal has no objective to verify the completion claim against.",
            )
        if not request.reason.strip():
            return GoalVerdict(
                confirmed=False,
                justification="The completion claim states no reason. Explain what was done before marking the Goal complete.",
            )
        evidence = request.evidence
        pending_checks = tuple(
            item for item in request.pending if item.kind in _CHECK_KINDS
        )
        if not evidence:
            return _reject(
                "This Turn has no finished work behind the claim (Goal bookkeeping "
                "such as get_goal does not count). Do and check the work, then "
                "claim completion.",
                request.pending,
            )
        checks = [item for item in evidence if item.kind in _CHECK_KINDS]
        if checks and checks[-1].status in _UNSUCCESSFUL:
            return _reject(
                _unsuccessful(
                    "check", checks[-1], "Fix the problem and run the check again"
                ),
                pending_checks,
            )
        if evidence[-1].status in _UNSUCCESSFUL:
            return _reject(
                _unsuccessful("step", evidence[-1], "Resolve it"),
                request.pending,
            )
        return GoalVerdict(
            confirmed=True,
            justification=(
                f"The latest of {len(evidence)} finished step(s) succeeded"
                + (", including the latest check." if checks else ".")
            ),
        )


def _reject(text: str, could_resolve: tuple[GoalEvidenceItem, ...]) -> GoalVerdict:
    if could_resolve:
        text += (
            f" {_describe(could_resolve[-1])} ran in the same step as this claim "
            "and has no recorded result yet; look at its result before claiming "
            "completion again."
        )
    return GoalVerdict(confirmed=False, justification=text)
