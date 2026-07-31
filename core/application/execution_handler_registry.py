"""Typed routing from durable Turn claims to execution handlers."""

from __future__ import annotations

import threading
from typing import Protocol

from core.application.execution_coordinator import (
    ExecutionCancellation,
    ExecutionDispatch,
    OrphanedExecution,
)
from core.domain.runtime_coordination import ResourceClaim
from core.domain.turn import TurnExecutor


class ExecutionHandler(Protocol):
    """Lifecycle implemented by one Turn executor."""

    def start_claimed_execution(self, dispatch: ExecutionDispatch) -> None: ...

    def fail_claimed_start(
        self,
        dispatch: ExecutionDispatch,
        error: Exception,
    ) -> None: ...

    def cancel_claimed_execution(self, claim: ResourceClaim) -> None: ...

    def recover_orphaned_execution(self, orphan: OrphanedExecution) -> None: ...

    def interrupt_unclaimed_queued_for_worker(self, worker_id: str) -> int: ...


class ExecutionHandlerRegistry:
    """Extensible enum-keyed dispatcher; never infers an executor from content."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[TurnExecutor, ExecutionHandler] = {}

    def register(
        self,
        executor: TurnExecutor,
        handler: ExecutionHandler,
    ) -> None:
        with self._lock:
            if executor in self._handlers:
                raise ValueError(
                    f"execution handler is already registered: {executor.value}"
                )
            self._handlers[executor] = handler

    def start_claimed_execution(self, dispatch: ExecutionDispatch) -> None:
        self._handler(dispatch.executor).start_claimed_execution(dispatch)

    def fail_claimed_start(
        self,
        dispatch: ExecutionDispatch,
        error: Exception,
    ) -> None:
        self._handler(dispatch.executor).fail_claimed_start(dispatch, error)

    def cancel_claimed_execution(
        self,
        request: ExecutionCancellation,
    ) -> None:
        self._handler(request.executor).cancel_claimed_execution(request.claim)

    def recover_orphaned_execution(self, orphan: OrphanedExecution) -> None:
        self._handler(orphan.executor).recover_orphaned_execution(orphan)

    def interrupt_unclaimed_queued_for_worker(self, worker_id: str) -> int:
        with self._lock:
            handlers = tuple(self._handlers.values())
        return sum(
            handler.interrupt_unclaimed_queued_for_worker(worker_id)
            for handler in handlers
        )

    def _handler(self, executor: TurnExecutor) -> ExecutionHandler:
        with self._lock:
            handler = self._handlers.get(executor)
        if handler is None:
            raise RuntimeError(
                f"no execution handler is registered for {executor.value}"
            )
        return handler


__all__ = ["ExecutionHandler", "ExecutionHandlerRegistry"]
