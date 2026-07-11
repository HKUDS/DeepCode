"""WorkerBackend — how the team runs one task (P4).

A worker is handed a task (a spec + a verification command) and an isolated
worktree, and must make the task's tests pass there. The first — and default —
backend is :class:`LoopWorker`: it simply runs a P3
:class:`~core.loop.task.LoopTask`, so a worker inherits the whole autonomous,
test-driven loop (rounds, real backpressure, checkpoints, failure ratchet) for
free. No new execution engine.

:class:`WorkerBackend` is a Protocol so the P4 external-CLI backend (drive
Claude Code / Codex headless — §4.5 HarnessAdapter) slots in later without
touching the coordinator: same ``run(task, workspace) -> WorkerResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.loop.backpressure import run_tests
from core.loop.task import LoopTask, RoundRunner, TestRunner


@dataclass
class WorkerResult:
    worker_id: str
    task_id: str
    succeeded: bool
    detail: str
    rounds: int


@runtime_checkable
class WorkerBackend(Protocol):
    async def run(self, task, workspace: str, *, worker_id: str) -> WorkerResult: ...


class LoopWorker:
    """Run a task to green with a P3 LoopTask inside the given worktree."""

    def __init__(
        self,
        *,
        model: str | None = None,
        max_rounds: int = 5,
        max_iterations: int = 40,
        round_runner: RoundRunner | None = None,
        test_runner: TestRunner = run_tests,
    ) -> None:
        self.model = model
        self.max_rounds = max_rounds
        self.max_iterations = max_iterations
        self._round_runner = round_runner
        self._test_runner = test_runner

    async def run(self, task, workspace: str, *, worker_id: str) -> WorkerResult:
        loop = LoopTask(
            goal=task.spec or task.title,
            workspace=workspace,
            test_command=task.test_command,
            model=self.model,
            max_rounds=self.max_rounds,
            max_iterations=self.max_iterations,
            round_runner=self._round_runner,
            test_runner=self._test_runner,
        )
        result = await loop.run()
        return WorkerResult(
            worker_id=worker_id,
            task_id=task.id,
            succeeded=result.succeeded,
            detail=result.state.stop_reason,
            rounds=result.state.round_count,
        )
