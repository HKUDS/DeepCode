"""Scheduling & keepalive (P3) — run loops repeatedly, safely.

Two pieces:

- :mod:`core.schedule.keepalive` — the *continuation gate* (ClawTeam's
  lesson): a long-horizon job continues only when it exited cleanly AND has
  not reached its goal AND is under its run cap. This is what prevents both
  premature death (stopping while there's still work) and runaway (looping
  forever).
- :mod:`core.schedule.scheduler` — a minimal interval runner that invokes a
  task and consults the gate between runs (self-wakeup / keepalive), with an
  injectable clock/sleep so it is deterministically testable.
"""

from core.schedule.keepalive import Continuation, ContinuationDecision
from core.schedule.scheduler import run_scheduled

__all__ = ["Continuation", "ContinuationDecision", "run_scheduled"]
