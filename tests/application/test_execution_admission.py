from __future__ import annotations

import pytest

from core.application.execution_admission import (
    ExecutionScope,
    SharedCapacityWorkspacePolicy,
)


def test_canonical_workspace_uses_project_fence_and_shared_capacity() -> None:
    policy = SharedCapacityWorkspacePolicy()

    options = policy.claim_options(
        ExecutionScope(
            thread_id="thread_alpha",
            project_id="proj_alpha",
            has_managed_worktree=False,
        ),
        max_concurrent_turns=2,
    )

    assert options == (
        (
            "capacity:global:turn:0",
            "thread:thread_alpha",
            "workspace:project:proj_alpha:canonical",
        ),
        (
            "capacity:global:turn:1",
            "thread:thread_alpha",
            "workspace:project:proj_alpha:canonical",
        ),
    )


def test_managed_worktree_uses_its_thread_owned_workspace_fence() -> None:
    policy = SharedCapacityWorkspacePolicy(capacity_namespace="capacity:test")

    options = policy.claim_options(
        ExecutionScope(
            thread_id="thread_worktree",
            project_id="proj_alpha",
            has_managed_worktree=True,
        ),
        max_concurrent_turns=1,
    )

    assert options == (
        (
            "capacity:test:0",
            "thread:thread_worktree",
            "workspace:worktree:thread_worktree",
        ),
    )


@pytest.mark.parametrize("value", [True, 0, -1])
def test_capacity_must_be_a_positive_integer(value: int) -> None:
    policy = SharedCapacityWorkspacePolicy()
    scope = ExecutionScope(
        thread_id="thread_alpha",
        project_id="proj_alpha",
        has_managed_worktree=False,
    )

    with pytest.raises(ValueError, match="positive"):
        policy.claim_options(scope, max_concurrent_turns=value)
