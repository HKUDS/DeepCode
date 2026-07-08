"""Stop policy — should the loop run another round? (P3)

A pure decision over :class:`~core.loop.state.LoopState`, evaluated after each
round. Rules are an ordered, declarative table (§3.4 mechanism, not scattered
``if``\\ s): the first matching rule wins. This is where the circuit
breakers live — tests-green success, the round-budget cap, and stall
detection (loop-until-dry's inverse: stop when rounds stop producing change).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.loop.state import (
    STATUS_EXHAUSTED,
    STATUS_RUNNING,
    STATUS_STALLED,
    STATUS_SUCCEEDED,
    LoopState,
)

_DEFAULT_STALL_ROUNDS = 3  # identical failing signature this many rounds → stalled


@dataclass(frozen=True)
class Decision:
    stop: bool
    status: str  # the LoopState status to set if stopping
    reason: str


def decide(state: LoopState, *, stall_rounds: int = _DEFAULT_STALL_ROUNDS) -> Decision:
    """Decide whether the loop should stop after its latest round."""
    last = state.last_round
    if last is None:
        return Decision(False, STATUS_RUNNING, "no rounds yet")

    # 1) Success: the real tests are green (only meaningful once tests ran).
    if last.tests_passed is True:
        return Decision(True, STATUS_SUCCEEDED, "tests pass")

    # 2) Budget: hit the round cap.
    if state.round_count >= state.max_rounds:
        return Decision(
            True,
            STATUS_EXHAUSTED,
            f"reached max_rounds ({state.max_rounds}) without passing tests",
        )

    # 3) Stall: the same failure signature for the last `stall_rounds` rounds
    #    means the agent is stuck going in circles — stop rather than burn
    #    the whole budget on a wall.
    if _is_stalled(state, stall_rounds):
        return Decision(
            True,
            STATUS_STALLED,
            f"no progress across {stall_rounds} rounds (identical test failure)",
        )

    return Decision(False, STATUS_RUNNING, "continue")


def _is_stalled(state: LoopState, stall_rounds: int) -> bool:
    if state.round_count < stall_rounds:
        return False
    recent = state.rounds[-stall_rounds:]
    signatures = {r.test_signature for r in recent}
    # A single, non-empty signature repeated across every recent round = stuck.
    # (Empty signature means tests passed or didn't run — not a stall.)
    return len(signatures) == 1 and "" not in signatures
