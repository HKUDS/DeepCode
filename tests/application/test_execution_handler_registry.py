from __future__ import annotations

from datetime import timedelta

import pytest

from core.application.execution_coordinator import (
    ExecutionCancellation,
    ExecutionDispatch,
    OrphanedExecution,
)
from core.application.execution_handler_registry import ExecutionHandlerRegistry
from core.domain import (
    ExecutionClass,
    ResourceClaim,
    ResourceLease,
    TurnExecutor,
)
from core.domain.common import utc_now


class RecordingHandler:
    def __init__(self) -> None:
        self.started: list[ExecutionDispatch] = []
        self.failed: list[tuple[ExecutionDispatch, Exception]] = []
        self.cancelled: list[ResourceClaim] = []
        self.orphans: list[OrphanedExecution] = []
        self.closed_workers: list[str] = []

    def start_claimed_execution(self, dispatch: ExecutionDispatch) -> None:
        self.started.append(dispatch)

    def fail_claimed_start(
        self,
        dispatch: ExecutionDispatch,
        error: Exception,
    ) -> None:
        self.failed.append((dispatch, error))

    def cancel_claimed_execution(self, claim: ResourceClaim) -> None:
        self.cancelled.append(claim)

    def recover_orphaned_execution(self, orphan: OrphanedExecution) -> None:
        self.orphans.append(orphan)

    def interrupt_unclaimed_queued_for_worker(self, worker_id: str) -> int:
        self.closed_workers.append(worker_id)
        return 1


def _claim() -> ResourceClaim:
    now = utc_now()
    lease = ResourceLease(
        resource_key="thread:thread_routed",
        epoch=1,
        holder_worker_id="worker_router",
        holder_turn_id="turn_routed",
        holder_turn_epoch=1,
        acquired_at=now,
        heartbeat_at=now + timedelta(milliseconds=1),
    )
    return ResourceClaim(
        worker_id="worker_router",
        turn_id="turn_routed",
        turn_epoch=1,
        leases=(lease,),
    )


def test_registry_routes_every_lifecycle_callback_by_typed_executor() -> None:
    handlers = ExecutionHandlerRegistry()
    agent = RecordingHandler()
    workflow = RecordingHandler()
    handlers.register(TurnExecutor.AGENT, agent)
    handlers.register(TurnExecutor.WORKFLOW, workflow)
    claim = _claim()
    dispatch = ExecutionDispatch(
        turn_id="turn_routed",
        thread_id="thread_routed",
        project_id="proj_routed",
        executor=TurnExecutor.WORKFLOW,
        execution_class=ExecutionClass.INTERACTIVE,
        claim=claim,
    )
    failure = RuntimeError("registry unavailable")
    orphan = OrphanedExecution(
        executor=TurnExecutor.WORKFLOW,
        status="running",
        claim=claim,
    )

    handlers.start_claimed_execution(dispatch)
    handlers.fail_claimed_start(dispatch, failure)
    handlers.cancel_claimed_execution(
        ExecutionCancellation(TurnExecutor.WORKFLOW, claim)
    )
    handlers.recover_orphaned_execution(orphan)

    assert agent.started == []
    assert workflow.started == [dispatch]
    assert workflow.failed == [(dispatch, failure)]
    assert workflow.cancelled == [claim]
    assert workflow.orphans == [orphan]
    assert handlers.interrupt_unclaimed_queued_for_worker("worker_router") == 2
    assert agent.closed_workers == ["worker_router"]
    assert workflow.closed_workers == ["worker_router"]


def test_registry_rejects_duplicate_or_missing_handler_registration() -> None:
    handlers = ExecutionHandlerRegistry()
    handler = RecordingHandler()
    handlers.register(TurnExecutor.AGENT, handler)
    with pytest.raises(ValueError, match="already registered"):
        handlers.register(TurnExecutor.AGENT, handler)

    claim = _claim()
    missing = ExecutionDispatch(
        turn_id="turn_routed",
        thread_id="thread_routed",
        project_id="proj_routed",
        executor=TurnExecutor.WORKFLOW,
        execution_class=ExecutionClass.INTERACTIVE,
        claim=claim,
    )
    with pytest.raises(RuntimeError, match="no execution handler"):
        handlers.start_claimed_execution(missing)
