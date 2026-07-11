"""Team intelligence (P4) — a lead decomposes a feature, workers build it.

Assembled from the P0–P3 foundations, not re-implemented:

- a **worker** is a P3 :class:`~core.loop.task.LoopTask` — it drives its
  assigned sub-task to green with real test backpressure;
- the **CriteriaExecutedGate** is P3's :func:`~core.loop.backpressure.run_tests`
  (verify by real execution, never the model's word — §8 anti-pattern #2);
- **isolation** is a git worktree per worker (parallel) + the P2.d shadow
  snapshots (per-worker undo);
- the **approval bridge** reuses P1's ``approval_callback`` + the P2-L5b web
  channel.

What is genuinely new is the *coordination*: a dependency-aware task board,
worktree orchestration, and the lead that decomposes and merges.

Public surface:

- :class:`~core.team.board.TaskBoard`, :class:`~core.team.board.Task`
- :class:`~core.team.worktree.WorktreeManager`, :class:`~core.team.worktree.MergeResult`
- :class:`~core.team.worker.WorkerBackend`, :class:`~core.team.worker.WorkerResult`
- :class:`~core.team.coordinator.Coordinator`
"""

from core.team.board import Task, TaskBoard
from core.team.coordinator import Coordinator, TeamEvent, TeamResult
from core.team.worker import LoopWorker, WorkerBackend, WorkerResult
from core.team.worktree import MergeResult, WorktreeManager

__all__ = [
    "Task",
    "TaskBoard",
    "WorktreeManager",
    "MergeResult",
    "WorkerBackend",
    "WorkerResult",
    "LoopWorker",
    "Coordinator",
    "TeamEvent",
    "TeamResult",
]
