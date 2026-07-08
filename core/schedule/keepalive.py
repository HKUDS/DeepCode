"""Keepalive continuation gate (P3).

The single decision a long-horizon runner asks between runs: *should I go
again?* ClawTeam's hard-won rule, made a pure function: continue **iff** the
last run exited cleanly (``$? == 0`` in spirit — no crash) **and** the goal
is not yet reached **and** the run cap has not been hit. Every clause guards a
real failure mode — a crash shouldn't be blindly retried, a finished job
shouldn't keep spinning, and nothing should loop forever.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContinuationDecision:
    go: bool
    reason: str


@dataclass(frozen=True)
class Continuation:
    """The keepalive gate. ``max_runs`` is the hard runaway backstop."""

    max_runs: int = 24

    def should_continue(
        self, *, runs_done: int, clean_exit: bool, goal_reached: bool
    ) -> ContinuationDecision:
        if goal_reached:
            return ContinuationDecision(False, "goal reached")
        if not clean_exit:
            return ContinuationDecision(False, "previous run did not exit cleanly")
        if runs_done >= self.max_runs:
            return ContinuationDecision(False, f"reached run cap ({self.max_runs})")
        return ContinuationDecision(True, "continue")
