"""Independent verification of a Goal completion claim.

The verifier answers one question: does this Turn's evidence support the
model's claim that the Goal is complete? It is consulted exactly once per
completion request, asynchronously, inside the goal runtime — there is no
synchronous side door and no caller-supplied "already verified" flag.

Rejections are data, not exceptions. A rejected claim returns its
justification to the model so the model can either produce the missing
evidence or declare the Goal blocked; a verifier that raises or cannot be
asked fails closed (rejects). After a configured number of rejections the
goal runtime moves the Goal to ``blocked`` rather than letting the model
burn Turns retrying the same claim.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GoalEvidenceItem:
    """One Turn evidence item, reduced to what a verifier judges."""

    kind: str
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class GoalCompletionRequest:
    """The claim under review: the real objective plus Turn evidence."""

    objective: str
    reason: str
    evidence: tuple[GoalEvidenceItem, ...]


@dataclass(frozen=True, slots=True)
class GoalVerdict:
    """The verifier's answer. ``justification`` always travels to the model."""

    confirmed: bool
    justification: str


class GoalCompletionVerifier(Protocol):
    """An async callable that judges one completion claim."""

    def __call__(self, request: GoalCompletionRequest) -> Awaitable[GoalVerdict]: ...


class RuleBasedVerifier:
    """Fail-closed structural checks over the objective, reason, and evidence.

    This is the built-in verifier behind the ``rule`` setting. It does not
    judge whether the work is *good*; it judges whether the claim is
    *supported*: a completion claim with no objective, no stated reason, or
    no recorded Turn evidence is rejected with a justification the model can
    act on.
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
        if not request.evidence:
            return GoalVerdict(
                confirmed=False,
                justification=(
                    "The Turn recorded no evidence (tool calls, file changes, or test "
                    "results) to support completion. Do the work first, then claim completion."
                ),
            )
        failed = [
            item
            for item in request.evidence
            if item.status.strip().lower() in {"failed", "error"}
        ]
        if failed:
            return GoalVerdict(
                confirmed=False,
                justification=(
                    f"{len(failed)} of {len(request.evidence)} evidence item(s) failed "
                    f"(latest: {failed[-1].summary!r}). Resolve the failures before completing."
                ),
            )
        return GoalVerdict(
            confirmed=True,
            justification=(
                f"{len(request.evidence)} Turn evidence item(s) support the claim."
            ),
        )


def model_backed_verifier(
    ask: Callable[[GoalCompletionRequest], Awaitable[str]],
) -> GoalCompletionVerifier:
    """Adapt a one-shot text answerer into a fail-closed verifier.

    ``ask`` returns the model's answer; anything other than an affirmative
    answer rejects, and an ``ask`` that raises rejects too (fail closed).
    """

    async def verify(request: GoalCompletionRequest) -> GoalVerdict:
        try:
            answer = (await ask(request)).strip().lower()
        except Exception as exc:  # noqa: BLE001 - fail closed, report to the model
            return GoalVerdict(
                confirmed=False,
                justification=f"The completion verifier failed: {exc}",
            )
        confirmed = answer.startswith("yes")
        return GoalVerdict(
            confirmed=confirmed,
            justification=(
                "The model verifier confirmed the evidence supports completion."
                if confirmed
                else "The model verifier rejected the completion claim."
            ),
        )

    return verify
