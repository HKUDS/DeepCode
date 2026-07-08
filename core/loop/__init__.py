"""Loop engineering (P3) — autonomous, test-driven multi-round execution.

The leap from "prompt the agent once" to "design a loop the agent runs to
completion on its own". The centrepiece is :class:`~core.loop.task.LoopTask`
(a Ralph loop): one thing per round, durable file-based state, real test
backpressure, shadow-git checkpoints, and a declarative stop policy — built by
assembling the P0–P2 foundations (AgentSession, Snapshotter, the native tools)
rather than re-implementing them.

Public surface:

- :class:`~core.loop.state.LoopState` / :class:`~core.loop.state.RoundRecord`
- :func:`~core.loop.backpressure.run_tests` / :class:`~core.loop.backpressure.TestResult`
- :func:`~core.loop.policy.decide` / :class:`~core.loop.policy.Decision`
- :class:`~core.loop.task.LoopTask` / :class:`~core.loop.task.LoopResult`
"""

from core.loop.backpressure import TestResult, run_tests
from core.loop.policy import Decision, decide
from core.loop.state import LoopState, RoundRecord

__all__ = [
    "LoopState",
    "RoundRecord",
    "TestResult",
    "run_tests",
    "Decision",
    "decide",
]
