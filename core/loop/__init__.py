"""Legacy loop data and verification helpers.

Production long-running work is now coordinated by Session Goals in
``core.application``. ``deepcode loop`` is a compatibility entry point over
that same GoalCoordinator. The types in this package remain importable for old
integrations and for projecting canonical Goal state to ``state.json``; they
are not a second product orchestration path.

Public surface:

- :class:`~core.loop.state.LoopState` / :class:`~core.loop.state.RoundRecord`
- :func:`~core.loop.backpressure.run_tests` / :class:`~core.loop.backpressure.TestResult`
- :func:`~core.loop.policy.decide` / :class:`~core.loop.policy.Decision`
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
