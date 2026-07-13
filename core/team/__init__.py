"""Worktree isolation for delegated sub-agents.

The deterministic ``deepcode team`` pipeline (a decompose→board→workers→merge
coordinator) has been retired in favour of model-driven delegation: the agent
calls the ``spawn_agent`` tool when it decides a subtask is worth handing off.
What survives here is the piece that delegation reuses — :class:`WorktreeManager`,
which gives a sub-agent an isolated git worktree and merges its result back with
3-way-merge conflict detection (``spawn_agent(isolate=true)``).
"""

from core.team.worktree import MergeResult, WorktreeError, WorktreeManager

__all__ = ["WorktreeManager", "MergeResult", "WorktreeError"]
