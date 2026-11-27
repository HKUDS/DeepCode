"""Minimal interval scheduler with keepalive gating (P3).

Runs a task, then between runs consults the :class:`Continuation` gate to
decide whether to self-wake and run again. The clock/sleep is injected, so
the whole self-wakeup behavior is deterministically testable without real
wall-clock waits — and a runaway is impossible because the gate's ``max_runs``
always bounds the loop.

Two optional control-plane layers sit on top (2026-09-19), both **inert by
default** so existing callers behave exactly as before:

- ``spend_gate`` (:class:`core.schedule.should_run.SpendGate`) is asked *before*
  each turn. It adds the one signal the keepalive gate cannot see: a brake on
  runs that report no progress (``RunOutcome.progress_made is False``).
- ``writeback`` / ``spend`` route every turn through
  :func:`core.schedule.turn_transaction.run_turn`, which enforces that quota is
  spent only after a validated, durable writeback. Asking for ``spend`` without
  a ``writeback`` raises rather than quietly spending.

See docs/2026-09-19_调研-LOOPX与DEEPCODE适配评估-report.md for the contracts
these two layers mirror.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.schedule.keepalive import Continuation
from core.schedule.should_run import ShouldRunDecision, SpendGate
from core.schedule.turn_transaction import TurnReceipt, ValidationResult, run_turn


@dataclass
class RunOutcome:
    """What one scheduled run reported back to the gate."""

    goal_reached: bool
    clean_exit: bool = True
    detail: str = ""
    # Progress is what the *task* can honestly claim; ``None`` means "not
    # reported" and is treated as neither progress nor stagnation.
    progress_made: bool | None = None


# A task receives the 0-based run index and returns its outcome.
ScheduledTask = Callable[[int], Awaitable[RunOutcome]]
RunHook = Callable[[int, RunOutcome], None]
TurnHook = Callable[[int, RunOutcome], Awaitable[None]]
DecisionHook = Callable[[ShouldRunDecision], None]


async def _run_one_turn(
    task: ScheduledTask,
    *,
    runs: int,
    validate: Callable[[RunOutcome], Awaitable[ValidationResult]] | None,
    writeback: TurnHook | None,
    spend: TurnHook | None,
) -> TurnReceipt:
    """One turn through the transaction; hooks see the 1-based run number."""
    turn_no = runs + 1
    return await run_turn(
        execute=lambda: task(runs),
        validate=validate,
        writeback=None
        if writeback is None
        else lambda outcome: writeback(turn_no, outcome),
        spend=None if spend is None else lambda outcome: spend(turn_no, outcome),
    )


async def run_scheduled(
    task: ScheduledTask,
    *,
    interval: float,
    gate: Continuation,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_run: RunHook | None = None,
    spend_gate: SpendGate | None = None,
    validate: Callable[[RunOutcome], Awaitable[ValidationResult]] | None = None,
    writeback: TurnHook | None = None,
    spend: TurnHook | None = None,
    on_decision: DecisionHook | None = None,
) -> list[RunOutcome]:
    """Run ``task`` repeatedly until the keepalive gate closes.

    A task that raises is recorded as an unclean exit (the gate then stops —
    a crash is not blindly retried). Returns the outcome of every run.

    When ``spend_gate`` is given it may close the loop *before* the next turn;
    ``on_decision`` sees every decision, including the refusal that stopped it.
    When ``writeback``/``spend`` are given, each turn goes through the turn
    transaction, so a failed writeback means the spend hook never fires.
    """
    if spend is not None and writeback is None:
        raise ValueError(
            "run_scheduled: `spend` requires `writeback` — quota is only spent "
            "after a durable writeback (see core.schedule.turn_transaction)"
        )

    outcomes: list[RunOutcome] = []
    runs = 0
    stagnant = 0
    last_clean_exit = True
    last_goal_reached = False

    while True:
        if spend_gate is not None:
            decision = spend_gate.should_run(
                runs_done=runs,
                clean_exit=last_clean_exit,
                goal_reached=last_goal_reached,
                stagnant_runs=stagnant,
            )
            if on_decision is not None:
                on_decision(decision)
            if not decision.should_run:
                break

        receipt = await _run_one_turn(
            task, runs=runs, validate=validate, writeback=writeback, spend=spend
        )
        if receipt.executed and isinstance(receipt.result, RunOutcome):
            outcome = receipt.result
        else:
            # A crashed turn, or a task that did not return a RunOutcome: both
            # are unclean exits, which the keepalive gate already refuses to
            # retry blindly.
            outcome = RunOutcome(
                False,
                clean_exit=False,
                detail=receipt.reason or "task did not return a RunOutcome",
            )

        runs += 1
        outcomes.append(outcome)
        if on_run is not None:
            on_run(runs, outcome)

        last_clean_exit = outcome.clean_exit
        last_goal_reached = outcome.goal_reached
        if outcome.progress_made is False:
            stagnant += 1
        elif outcome.progress_made is True:
            stagnant = 0

        decision = gate.should_continue(
            runs_done=runs,
            clean_exit=outcome.clean_exit,
            goal_reached=outcome.goal_reached,
        )
        if not decision.go:
            break
        await sleep(interval)
    return outcomes
