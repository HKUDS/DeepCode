"""Turn transaction (P1) — spend only after a validated durable writeback.

LoopX's turn contract (primary source: ``loopx/control_plane/
turn_transaction_contract.json``, schema ``loopx_turn_transaction_contract_v0``)
names seven phases::

    host_execute → typed_result → validation → durable_writeback
                 → quota_spend → scheduler_apply → scheduler_ack

DEEPCODE has no scheduler_apply/ack split yet, so this module implements the
first five — the part that protects money. The ordering *is* the feature:

**quota is spent only after the turn's result has been validated and durably
written back.** A crash, an untyped result, a validation failure or a failed
writeback all end the turn *before* the spend phase.

Why this matters here specifically: the 2026-09 compaction incident burned
~330M prompt tokens because a turn could "succeed" every time while nothing
durable changed. "No progress, no spend" stated as an ordering invariant is
what makes that impossible to repeat silently. A spend failure is *not* rolled
back either — durable work stays durable; only the meter is left unsettled.

The four callables are injected and async, matching the async loop that drives
them, so the contract is testable without a host, a clock or a quota service —
the same convention :mod:`core.schedule.scheduler` uses for its sleep function.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

# The phase names, in the order the contract requires them to happen.
PHASES: tuple[str, ...] = (
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ValidationResult:
    """Whether a turn's result is well-formed enough to be written back."""

    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class TurnReceipt:
    """What actually happened, in order. ``spent`` is the headline field."""

    phases: tuple[str, ...]
    outcome: str
    executed: bool = False
    validated: bool = False
    written_back: bool = False
    spent: bool = False
    reason: str = ""
    # The executed result, passed through for the caller's own use. Untyped on
    # purpose: it is a dynamic pass-through, not a contract this module reads.
    result: object | None = None


async def run_turn(
    *,
    execute: Callable[[], Awaitable[_T]],
    validate: Callable[[_T], Awaitable[ValidationResult]] | None = None,
    writeback: Callable[[_T], Awaitable[None]] | None = None,
    spend: Callable[[_T], Awaitable[None]] | None = None,
) -> TurnReceipt:
    """Run one turn under the writeback-before-spend ordering.

    ``validate`` defaults to accepting (no validator = the host asserts its own
    result is typed); ``writeback``/``spend`` default to absent, in which case
    the turn stops before the phase that has no hook. Asking to ``spend``
    without a ``writeback`` is a wiring error and raises instead of quietly
    spending.
    """
    if spend is not None and writeback is None:
        raise ValueError(
            "spend requires a writeback: quota is only spent after a durable writeback"
        )

    phases: list[str] = ["host_execute"]
    try:
        result = await execute()
    except Exception as exc:  # noqa: BLE001 - a crashed turn is a typed outcome
        return TurnReceipt(tuple(phases), "execute_failed", reason=str(exc))

    phases.append("typed_result")
    phases.append("validation")
    if validate is not None:
        verdict = await validate(result)
        if not verdict.ok:
            return TurnReceipt(
                tuple(phases),
                "invalid_result",
                executed=True,
                reason=verdict.reason,
                result=result,
            )

    if writeback is None:
        return TurnReceipt(
            tuple(phases), "no_writeback", executed=True, validated=True, result=result
        )

    phases.append("durable_writeback")
    try:
        await writeback(result)
    except Exception as exc:  # noqa: BLE001 - a failed writeback blocks the spend
        return TurnReceipt(
            tuple(phases),
            "writeback_failed",
            executed=True,
            validated=True,
            reason=str(exc),
            result=result,
        )

    if spend is None:
        return TurnReceipt(
            tuple(phases),
            "spend_skipped",
            executed=True,
            validated=True,
            written_back=True,
            result=result,
        )

    phases.append("quota_spend")
    try:
        await spend(result)
    except Exception as exc:  # noqa: BLE001 - the writeback stands; the meter did not
        return TurnReceipt(
            tuple(phases),
            "spend_failed",
            executed=True,
            validated=True,
            written_back=True,
            reason=str(exc),
            result=result,
        )

    return TurnReceipt(
        tuple(phases),
        "spent",
        executed=True,
        validated=True,
        written_back=True,
        spent=True,
        result=result,
    )
