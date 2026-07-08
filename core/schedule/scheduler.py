"""Minimal interval scheduler with keepalive gating (P3).

Runs a task, then between runs consults the :class:`Continuation` gate to
decide whether to self-wake and run again. The clock/sleep is injected, so
the whole self-wakeup behavior is deterministically testable without real
wall-clock waits — and a runaway is impossible because the gate's ``max_runs``
always bounds the loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.schedule.keepalive import Continuation


@dataclass
class RunOutcome:
    """What one scheduled run reported back to the gate."""

    goal_reached: bool
    clean_exit: bool = True
    detail: str = ""


# A task receives the 0-based run index and returns its outcome.
ScheduledTask = Callable[[int], Awaitable[RunOutcome]]
RunHook = Callable[[int, RunOutcome], None]


async def run_scheduled(
    task: ScheduledTask,
    *,
    interval: float,
    gate: Continuation,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_run: RunHook | None = None,
) -> list[RunOutcome]:
    """Run ``task`` repeatedly until the keepalive gate closes.

    A task that raises is recorded as an unclean exit (the gate then stops —
    a crash is not blindly retried). Returns the outcome of every run.
    """
    outcomes: list[RunOutcome] = []
    runs = 0
    while True:
        try:
            outcome = await task(runs)
        except Exception as exc:  # noqa: BLE001 - a crash is an unclean exit, not fatal
            outcome = RunOutcome(False, clean_exit=False, detail=str(exc))
        runs += 1
        outcomes.append(outcome)
        if on_run is not None:
            on_run(runs, outcome)

        decision = gate.should_continue(
            runs_done=runs,
            clean_exit=outcome.clean_exit,
            goal_reached=outcome.goal_reached,
        )
        if not decision.go:
            break
        await sleep(interval)
    return outcomes
