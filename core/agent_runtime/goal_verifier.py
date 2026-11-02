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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from core.agent_runtime.goal_checks import GoalCheckRun, GoalCheckSpec, run_checks

# What a kept review may hold: enough to read in the Goal's history, never the
# whole output of every command.
_RECORD_TEXT_MAX_CHARS = 2_000
_RECORD_OUTPUT_MAX_CHARS = 1_500
_REJECTION_OUTPUT_MAX_CHARS = 1_500


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
    # Changes whenever the agent changes files for this Goal: rejected claims
    # with the same marker in a row made no progress in between.
    progress_marker: str = ""
    # The checklist the user confirmed, and how its commands went this time
    # (filled in by the completion gate before a review).
    checks: tuple[GoalCheckSpec, ...] = ()
    check_runs: tuple[GoalCheckRun, ...] = ()


class VerificationUnavailable(RuntimeError):
    """The verifier could not reach a verdict (a provider error, a timeout).

    Not a rejection: the work may be fine. The Goal runtime pauses the Goal
    instead of counting it against the agent. ``tokens`` is what the attempt
    spent anyway.
    """

    def __init__(self, message: str, *, tokens: int = 0) -> None:
        super().__init__(message)
        self.tokens = tokens
        # Confirmed checks that ran before the review failed.
        self.check_runs: tuple[GoalCheckRun, ...] = ()


@dataclass(frozen=True, slots=True)
class RequirementFinding:
    """One requirement as the reviewer judged it (see goal_reviewer.FINDINGS)."""

    requirement: str
    finding: str
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class GoalVerdict:
    """The verifier's answer. ``justification`` always travels to the model.

    ``impossible`` marks a rejection that no further work can turn around:
    the Goal runtime then blocks the Goal at once instead of waiting for
    more rejected claims. ``tokens`` is what reaching the verdict cost; the
    Goal is charged for it.
    """

    confirmed: bool
    justification: str
    impossible: bool = False
    tokens: int = 0
    # Which step reached the verdict: "rule", "checks" or "reviewer"; "none"
    # when nothing was checked (no verifier and no confirmed checks).
    stage: str = ""
    summary: str = ""
    requirements: tuple[RequirementFinding, ...] = ()
    checked: tuple[str, ...] = ()
    check_runs: tuple[GoalCheckRun, ...] = ()

    def __post_init__(self) -> None:
        if self.confirmed and self.impossible:
            raise ValueError("a confirmed verdict cannot also be impossible")
        if self.tokens < 0:
            raise ValueError("tokens cannot be negative")


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

    A command's status is its exit code, which for a pipeline is its last
    command's: ``pytest | tail`` reads as passed when the tests fail. A
    failing earlier stage cannot tell a failed check from a harmless one
    (``grep`` with no match, a writer cut off by ``head``), so it is not used;
    the agent's prompt asks for checks without pipes, and the reviewer re-runs
    them.

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
                stage="rule",
            )
        if not request.reason.strip():
            return GoalVerdict(
                confirmed=False,
                justification="The completion claim states no reason. Explain what was done before marking the Goal complete.",
                stage="rule",
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
            stage="rule",
        )


def _reject(text: str, could_resolve: tuple[GoalEvidenceItem, ...]) -> GoalVerdict:
    if could_resolve:
        text += (
            f" {_describe(could_resolve[-1])} ran in the same step as this claim "
            "and has no recorded result yet; look at its result before claiming "
            "completion again."
        )
    return GoalVerdict(confirmed=False, justification=text, stage="rule")


CheckRunner = Callable[[tuple[GoalCheckSpec, ...]], Awaitable[tuple[GoalCheckRun, ...]]]


class CompletionGate:
    """Every completion claim goes through here, cheapest step first.

    1. The rule check, unless ``goal.completionVerifier`` is ``off``.
    2. The Goal's confirmed checks: DeepCode runs each command itself; one
       that fails turns the claim back with its output.
    3. The reviewer, when ``goal.completionVerifier`` is ``reviewer``; it is
       told how the checks went.

    With the setting ``off`` and no confirmed checks, nothing is checked and
    the verdict's ``stage`` is ``none``.
    """

    def __init__(
        self,
        *,
        rule: bool,
        review: GoalCompletionVerifier | None = None,
        run: CheckRunner | None = None,
        workspace: str = ".",
        sandbox_enabled: bool | None = None,
    ) -> None:
        """``sandbox_enabled``: the agent's own command sandbox setting, so a
        check runs with the same rights as the agent's commands."""

        self._rule = RuleBasedVerifier() if rule or review is not None else None
        self._review = review
        self._run = run or (lambda checks: _run_in(checks, workspace, sandbox_enabled))

    async def __call__(self, request: GoalCompletionRequest) -> GoalVerdict:
        verdict = GoalVerdict(confirmed=True, justification="", stage="none")
        if self._rule is not None:
            verdict = await self._rule(request)
            if not verdict.confirmed:
                return verdict
        runs: tuple[GoalCheckRun, ...] = ()
        if any(check.command for check in request.checks):
            runs = await self._run(request.checks)
            failed = [run for run in runs if not run.passed]
            if failed:
                return GoalVerdict(
                    confirmed=False,
                    justification=_failed_checks_text(runs, failed),
                    stage="checks",
                    check_runs=runs,
                )
            verdict = GoalVerdict(
                confirmed=True,
                justification=(
                    f"DeepCode ran the {len(runs)} confirmed check(s) and each "
                    "exited 0."
                ),
                stage="checks",
                check_runs=runs,
            )
        if self._review is None:
            return verdict
        try:
            reviewed = await self._review(replace(request, check_runs=runs))
        except VerificationUnavailable as exc:
            exc.check_runs = runs
            raise
        return replace(reviewed, check_runs=runs)


async def _run_in(
    checks: tuple[GoalCheckSpec, ...], workspace: str, sandbox_enabled: bool | None
) -> tuple[GoalCheckRun, ...]:
    from core.harness.env_sanitize import child_env

    return await run_checks(
        checks, cwd=workspace, env=child_env(), sandbox_enabled=sandbox_enabled
    )


def _failed_checks_text(
    runs: tuple[GoalCheckRun, ...], failed: list[GoalCheckRun]
) -> str:
    lines = [
        f"DeepCode ran the Goal's {len(runs)} confirmed check(s); "
        f"{len(failed)} did not pass:"
    ]
    for run in failed:
        lines.append(f"- {run.requirement}: {run.describe()}")
        output = run.output.strip()
        if output:
            if len(output) > _REJECTION_OUTPUT_MAX_CHARS:
                output = "…" + output[-(_REJECTION_OUTPUT_MAX_CHARS - 1) :]
            lines.append("  Output (end):\n" + _indent(output))
    lines.append(
        "Fix the work so these commands pass, then claim completion again. "
        "Do not change the checks or what they test to make them pass."
    )
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def review_record(
    *,
    goal_id: str,
    turn_id: str,
    result: str,
    verdict: GoalVerdict | None = None,
    problem: str = "",
    check_runs: tuple[GoalCheckRun, ...] = (),
    tokens: int = 0,
) -> dict[str, Any]:
    """One completion review as the Goal's history keeps it.

    ``result`` is ``passed``, ``rejected``, ``impossible`` or ``unavailable``
    (the review could not run; ``problem`` says why).
    """

    runs = verdict.check_runs if verdict is not None else check_runs
    return {
        "goalId": goal_id,
        "turnId": turn_id,
        "recordedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "stage": (verdict.stage or "other") if verdict is not None else "reviewer",
        "result": result,
        "justification": _clip(
            verdict.justification if verdict is not None else problem,
            _RECORD_TEXT_MAX_CHARS,
        ),
        "summary": _clip(verdict.summary, _RECORD_TEXT_MAX_CHARS)
        if verdict is not None
        else "",
        "requirements": [
            {
                "requirement": _clip(entry.requirement, 500),
                "finding": entry.finding,
                "evidence": _clip(entry.evidence, 800),
            }
            for entry in (verdict.requirements if verdict is not None else ())
        ],
        "checks": [
            {
                "requirement": run.requirement,
                "command": list(run.command),
                "exitCode": run.exit_code,
                "problem": run.problem,
                "seconds": round(run.seconds, 1),
                "output": (
                    run.output
                    if len(run.output) <= _RECORD_OUTPUT_MAX_CHARS
                    else "…" + run.output[-(_RECORD_OUTPUT_MAX_CHARS - 1) :]
                ),
            }
            for run in runs
        ],
        "checked": [
            _clip(entry, 300)
            for entry in (verdict.checked if verdict is not None else ())
        ][:20],
        "tokens": max(0, verdict.tokens if verdict is not None else tokens),
    }
