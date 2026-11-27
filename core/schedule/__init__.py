"""Scheduling & keepalive (P3) — run loops repeatedly, safely.

Four pieces:

- :mod:`core.schedule.keepalive` — the *continuation gate* (ClawTeam's
  lesson): a long-horizon job continues only when it exited cleanly AND has
  not reached its goal AND is under its run cap. This is what prevents both
  premature death (stopping while there's still work) and runaway (looping
  forever).
- :mod:`core.schedule.should_run` — the *pre-turn spend gate* (2026-09-19): a
  typed, fail-closed decision envelope that adds the signal the continuation
  gate cannot see — whether the recent runs bought any progress at all.
- :mod:`core.schedule.turn_transaction` — the *turn transaction* (2026-09-19):
  orders one turn as execute → typed result → validation → durable writeback →
  spend, so quota is never spent on a turn that did not write back.
- :mod:`core.schedule.scheduler` — a minimal interval runner that invokes a
  task and consults the gates between runs (self-wakeup / keepalive), with an
  injectable clock/sleep so it is deterministically testable.
"""

from core.schedule.keepalive import Continuation, ContinuationDecision
from core.schedule.scheduler import RunOutcome, run_scheduled
from core.schedule.should_run import ShouldRunDecision, SpendGate
from core.schedule.turn_transaction import (
    PHASES,
    TurnReceipt,
    ValidationResult,
    run_turn,
)

__all__ = [
    "PHASES",
    "Continuation",
    "ContinuationDecision",
    "RunOutcome",
    "ShouldRunDecision",
    "SpendGate",
    "TurnReceipt",
    "ValidationResult",
    "run_scheduled",
    "run_turn",
]
