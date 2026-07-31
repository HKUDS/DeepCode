"""Pure resource policy for admitting durable Turn execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.domain.common import require_non_empty, require_prefixed_id


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    """Persisted workspace identity used for conflict isolation."""

    thread_id: str
    project_id: str
    has_managed_worktree: bool

    def __post_init__(self) -> None:
        require_non_empty(self.thread_id, "thread_id")
        require_prefixed_id(self.project_id, "proj")


class ExecutionAdmissionPolicy(Protocol):
    """Return ordered, atomic resource sets that may admit one Turn."""

    def claim_options(
        self,
        scope: ExecutionScope,
        *,
        max_concurrent_turns: int,
    ) -> tuple[tuple[str, ...], ...]: ...


@dataclass(frozen=True, slots=True)
class SharedCapacityWorkspacePolicy:
    """Bound total concurrency while allowing managed worktrees to run apart.

    Canonical Project workspaces share one write fence. A managed worktree is
    owned by exactly one Thread, so its Thread id is its stable isolation
    identity. Capacity is shared by every worker using the same database.
    """

    capacity_namespace: str = "capacity:global:turn"

    def __post_init__(self) -> None:
        require_non_empty(self.capacity_namespace, "capacity_namespace")

    def claim_options(
        self,
        scope: ExecutionScope,
        *,
        max_concurrent_turns: int,
    ) -> tuple[tuple[str, ...], ...]:
        if (
            isinstance(max_concurrent_turns, bool)
            or max_concurrent_turns < 1
        ):
            raise ValueError("max_concurrent_turns must be positive")
        workspace_key = (
            f"workspace:worktree:{scope.thread_id}"
            if scope.has_managed_worktree
            else f"workspace:project:{scope.project_id}:canonical"
        )
        common = (f"thread:{scope.thread_id}", workspace_key)
        return tuple(
            (f"{self.capacity_namespace}:{slot}", *common)
            for slot in range(max_concurrent_turns)
        )


__all__ = [
    "ExecutionAdmissionPolicy",
    "ExecutionScope",
    "SharedCapacityWorkspacePolicy",
]
