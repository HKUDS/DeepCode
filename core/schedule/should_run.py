"""Pre-turn spend gate (P0) — a typed, fail-closed decision envelope.

The keepalive gate (:mod:`core.schedule.keepalive`) answers *may I go again?*
from three signals: clean exit, goal reached, run cap. It cannot answer the
question a long-horizon loop actually burns money on: **is there any useful
state transition left to buy?** A loop that exits cleanly every single time and
never reaches its goal passes the keepalive gate until the cap runs out — that
is exactly the shape of the 2026-09 compaction incident, where every turn
"succeeded" while the context grew monotonically (~330M prompt tokens).

This gate closes that hole the way LoopX's ``quota should-run`` does
(primary source: docs/2026-09-19_调研-LOOPX与DEEPCODE适配评估-report.md §6.3):

- the answer is a **typed envelope** — ``decision`` / ``state`` / ``reason`` /
  ``recommended_action`` — so a caller never parses prose to decide;
- it is **fail-closed**: missing, negative or contradictory input yields
  ``should_run=False``. There is no input combination that defaults to "run";
- a *refusal* is a first-class outcome carrying the same fields as an approval.

Honest limit: the progress clause refuses only on **evidence** of stagnation —
``stagnant_runs`` counted by the caller from a real signal. A caller with no
progress signal passes 0, and the loop falls back to the run cap. This gate
never upgrades a guess into permission, nor into a refusal.

Not here on purpose: quota accounting, leases, writeback. Those belong to the
turn transaction (:mod:`core.schedule.turn_transaction`).
"""

from __future__ import annotations

from dataclasses import dataclass

# The envelope's states. ``ready`` is the only one that permits a run; the rest
# name *why* the loop stopped, and each maps to a different operator action.
STATE_READY = "ready"
STATE_COMPLETE = "complete"
STATE_EXHAUSTED = "exhausted"
STATE_NO_PROGRESS = "blocked_no_progress"
STATE_UNHEALTHY = "blocked_health"
STATE_INVALID = "blocked_validation"


@dataclass(frozen=True)
class ShouldRunDecision:
    """The typed answer to "may this loop spend another turn?"."""

    should_run: bool
    decision: str
    state: str
    reason: str
    recommended_action: str
    waiting_on: str | None = None
    runs_done: int | None = None

    def as_dict(self) -> dict[str, object]:
        """Flat dict for logs/ledgers — same shape as LoopX's JSON envelope."""
        return {
            "decision": self.decision,
            "should_run": self.should_run,
            "state": self.state,
            "reason": self.reason,
            "recommended_action": self.recommended_action,
            "waiting_on": self.waiting_on,
            "runs_done": self.runs_done,
        }


def _deny(
    state: str,
    reason: str,
    recommended_action: str,
    runs_done: int,
    waiting_on: str | None,
) -> ShouldRunDecision:
    return ShouldRunDecision(
        should_run=False,
        decision="skip",
        state=state,
        reason=reason,
        recommended_action=recommended_action,
        waiting_on=waiting_on,
        runs_done=runs_done,
    )


@dataclass(frozen=True)
class SpendGate:
    """Permission gate consulted *before* each turn.

    ``max_runs`` is the same hard runaway backstop the keepalive gate uses.
    ``max_stagnant_runs`` is the new brake: stop after N consecutive runs that
    reported no progress. ``0`` disables the brake (the default) so existing
    callers keep their exact behavior until they opt in.
    """

    max_runs: int = 24
    max_stagnant_runs: int = 0

    def should_run(
        self,
        *,
        runs_done: int,
        clean_exit: bool,
        goal_reached: bool,
        stagnant_runs: int = 0,
        waiting_on: str | None = None,
    ) -> ShouldRunDecision:
        """Decide whether the next turn may run.

        Precedence is deliberate: unreadable input, then done, then unhealthy,
        then out of budget, then stalled, then run.
        """
        if (
            runs_done < 0
            or stagnant_runs < 0
            or self.max_runs < 0
            or self.max_stagnant_runs < 0
        ):
            return _deny(
                STATE_INVALID,
                (
                    "invalid counters "
                    f"(runs_done={runs_done}, stagnant_runs={stagnant_runs}, "
                    f"max_runs={self.max_runs}, max_stagnant_runs={self.max_stagnant_runs})"
                ),
                "fix the caller's bookkeeping before spending another turn",
                runs_done,
                waiting_on,
            )

        if not clean_exit and goal_reached:
            return _deny(
                STATE_INVALID,
                "contradictory outcome: goal reached on a run that did not exit cleanly",
                "re-check how the run outcome was recorded",
                runs_done,
                waiting_on,
            )

        if goal_reached:
            return _deny(
                STATE_COMPLETE,
                "goal reached",
                "stop scheduling this goal",
                runs_done,
                waiting_on,
            )

        if not clean_exit:
            return _deny(
                STATE_UNHEALTHY,
                "previous run did not exit cleanly",
                "inspect the crash before spending another turn",
                runs_done,
                waiting_on,
            )

        if runs_done >= self.max_runs:
            return _deny(
                STATE_EXHAUSTED,
                f"reached run cap ({self.max_runs})",
                "raise the run cap, or replan the goal",
                runs_done,
                waiting_on,
            )

        if self.max_stagnant_runs and stagnant_runs >= self.max_stagnant_runs:
            return _deny(
                STATE_NO_PROGRESS,
                f"no progress for {stagnant_runs} consecutive run(s)",
                "replan, or ask the owner a concrete question and wait",
                runs_done,
                waiting_on or "owner",
            )

        return ShouldRunDecision(
            should_run=True,
            decision="run",
            state=STATE_READY,
            reason="healthy, under the run cap, progress not exhausted",
            recommended_action="spend the turn",
            waiting_on=waiting_on,
            runs_done=runs_done,
        )
