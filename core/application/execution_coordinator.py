"""Cross-process admission in front of the existing execution registry."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.application.execution_admission import (
    ExecutionAdmissionPolicy,
    ExecutionScope,
    SharedCapacityWorkspacePolicy,
)
from core.domain.common import new_id, require_aware, require_prefixed_id, utc_now
from core.domain.runtime_coordination import (
    ExecutionClass,
    ResourceClaim,
    RuntimeWorker,
)
from core.domain.turn import TurnExecutor
from core.file_lock import FileLease
from core.persistence.coordination_repository import (
    QueuedTurnCandidate,
    RuntimeCoordinationRepository,
)
from core.persistence.database import Database

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class ExecutionDispatch:
    """A fenced Turn that is ready to enter the existing ExecutionRegistry."""

    turn_id: str
    thread_id: str
    project_id: str
    executor: TurnExecutor
    execution_class: ExecutionClass
    claim: ResourceClaim


@dataclass(frozen=True, slots=True)
class ExecutionCancellation:
    """A durable cancellation request routed to its typed executor."""

    executor: TurnExecutor
    claim: ResourceClaim

    @property
    def turn_id(self) -> str:
        assert self.claim.turn_id is not None
        return self.claim.turn_id


@dataclass(frozen=True, slots=True)
class OrphanedExecution:
    """Dead-worker work that must be settled, never silently replayed."""

    executor: TurnExecutor
    status: str
    claim: ResourceClaim

    @property
    def turn_id(self) -> str:
        assert self.claim.turn_id is not None
        return self.claim.turn_id


@dataclass(frozen=True, slots=True)
class DeadWorkerRecovery:
    """Selective recovery result after an OS lock proved worker death."""

    worker_id: str
    rehomed_turn_ids: tuple[str, ...]
    released_queued_turn_ids: tuple[str, ...]
    released_worker_resource_keys: tuple[str, ...]
    requires_settlement: tuple[OrphanedExecution, ...]


ExecutionStarter = Callable[[ExecutionDispatch], None]
StartFailureHandler = Callable[[ExecutionDispatch, Exception], None]
OrphanSettlementHandler = Callable[[OrphanedExecution], None]
CancellationHandler = Callable[[ExecutionCancellation], None]


class ExecutionCoordinator:
    """Admit durable Turn heads without owning another execution runtime."""

    def __init__(
        self,
        database: Database,
        start_execution: ExecutionStarter,
        *,
        max_concurrent_turns: int = 2,
        worker_id: str | None = None,
        surface: str = "application",
        pid: int | None = None,
        heartbeat_interval: float = 2.0,
        stale_worker_after: float = 15.0,
        clock: Clock = utc_now,
        liveness_directory: Path | None = None,
        admission_policy: ExecutionAdmissionPolicy | None = None,
        on_start_failure: StartFailureHandler | None = None,
        on_orphaned_execution: OrphanSettlementHandler | None = None,
        on_cancel_requested: CancellationHandler | None = None,
    ) -> None:
        if isinstance(max_concurrent_turns, bool) or max_concurrent_turns < 1:
            raise ValueError("max_concurrent_turns must be positive")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        if stale_worker_after <= heartbeat_interval:
            raise ValueError(
                "stale_worker_after must be greater than heartbeat_interval"
            )
        resolved_worker_id = worker_id or new_id("worker")
        require_prefixed_id(resolved_worker_id, "worker")
        if not surface.strip():
            raise ValueError("surface must not be empty")
        resolved_pid = pid if pid is not None else os.getpid()
        if isinstance(resolved_pid, bool) or resolved_pid < 1:
            raise ValueError("pid must be positive")

        self.database = database
        self._start_execution = start_execution
        self.max_concurrent_turns = max_concurrent_turns
        self.worker_id = resolved_worker_id
        self.surface = surface
        self.pid = resolved_pid
        self.heartbeat_interval = heartbeat_interval
        self.stale_worker_after = stale_worker_after
        self._clock = clock
        self._admission_policy = admission_policy or SharedCapacityWorkspacePolicy()
        self._on_start_failure = on_start_failure
        self._on_orphaned_execution = on_orphaned_execution
        self._on_cancel_requested = on_cancel_requested
        self._liveness_directory = (
            liveness_directory
            if liveness_directory is not None
            else database.path.parent / f"{database.path.name}.runtime-workers"
        )

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._liveness_lease: FileLease | None = None
        self._worker: RuntimeWorker | None = None
        self._claims: dict[str, ResourceClaim] = {}
        self._quiesced = False
        self._closed = False

    @property
    def worker(self) -> RuntimeWorker | None:
        with self._lock:
            return self._worker

    @property
    def active_claims(self) -> tuple[ResourceClaim, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._claims.values(),
                    key=lambda claim: (claim.turn_id or "", claim.turn_epoch or 0),
                )
            )

    def worker_lock_path(self, worker_id: str) -> Path:
        require_prefixed_id(worker_id, "worker")
        return self._liveness_directory / f"{worker_id}.lock"

    def start(self, *, background: bool = True) -> RuntimeWorker:
        """Register one worker and hold its exclusive OS lock for its lifetime."""

        with self._lock:
            if self._closed:
                raise RuntimeError("execution coordinator is closed")
            if self._worker is not None:
                raise RuntimeError("execution coordinator is already started")

            lease = FileLease.acquire(
                self.worker_lock_path(self.worker_id),
                shared=False,
                blocking=False,
            )
            if lease is None:
                raise RuntimeError(f"worker liveness lock is held: {self.worker_id}")
            now = self._now()
            worker = RuntimeWorker(
                id=self.worker_id,
                pid=self.pid,
                surface=self.surface,
                started_at=now,
                heartbeat_at=now,
            )
            try:
                self._register_worker_verified(worker)
            except BaseException:
                lease.close()
                raise

            self._liveness_lease = lease
            self._worker = worker
            self._stop.clear()
            self._wake.clear()
            self._quiesced = False
            if background:
                self._start_background_locked()
            return worker

    def _register_worker_verified(self, worker: RuntimeWorker) -> None:
        """Register and read the row back on a fresh connection.

        Under concurrent process startup, a committed registration has been
        observed missing from the database every later read sees — the cause
        of a FOREIGN KEY failure at this worker's first turn submit, minutes
        later and naming nothing. Verifying here converts that into either a
        silent self-heal (one retry) or a startup error that says what
        actually happened. (Root cause investigated 2026-08-19: the row
        vanished under concurrent worker startup; the write itself raised
        nothing.)
        """
        for attempt in (1, 2):
            with self.database.transaction() as connection:
                repository = RuntimeCoordinationRepository(connection)
                if repository.get_worker(worker.id) is None:
                    repository.register_worker(worker)
            with self.database.read() as connection:
                readback = RuntimeCoordinationRepository(connection).get_worker(
                    worker.id
                )
            if readback is not None:
                if attempt > 1:
                    logger.warning(
                        "Worker registration %s required a retry to become "
                        "durable; a concurrent process start likely raced it",
                        worker.id,
                    )
                return
        raise RuntimeError(
            f"worker registration did not become durable: {worker.id}. "
            "Another DeepCode process starting at the same moment likely "
            "raced this one; retry, or start the processes a moment apart."
        )

    def start_background(self) -> None:
        """Begin admission after startup recovery has reached a stable boundary."""

        self._require_started()
        with self._lock:
            if self._quiesced or self._stop.is_set():
                raise RuntimeError("execution coordinator is quiesced")
            if self._thread is not None:
                return
            self._start_background_locked()

    def offer(self) -> None:
        """Wake the admission loop after durable queued work changes."""

        self._wake.set()

    def heartbeat(self) -> tuple[ResourceClaim, ...]:
        """Refresh worker and owned claim facts, returning any lost fences."""

        self._require_started()
        now = self._now()
        with self._lock:
            claims = tuple(self._claims.values())
        stale: list[ResourceClaim] = []
        with self.database.transaction() as connection:
            coordination = RuntimeCoordinationRepository(connection)
            if not coordination.heartbeat_worker(self.worker_id, now):
                raise RuntimeError("runtime worker heartbeat was rejected")
            for claim in claims:
                if not coordination.heartbeat_claim(claim, observed_at=now):
                    stale.append(claim)
        return tuple(stale)

    def dispatch_once(
        self, *, candidate_limit: int = 128
    ) -> tuple[ExecutionDispatch, ...]:
        """Claim eligible work, then send admitted Turns to the injected starter."""

        self._require_dispatchable()
        now = self._now()
        claimed: list[ExecutionDispatch] = []
        with self.database.transaction() as connection:
            coordination = RuntimeCoordinationRepository(connection)
            candidates = coordination.list_queued_turn_candidates(
                self.worker_id,
                limit=candidate_limit,
            )
            for candidate in candidates:
                claim = self._claim_candidate(
                    coordination,
                    candidate,
                    acquired_at=now,
                )
                if claim is None:
                    continue
                claimed.append(
                    ExecutionDispatch(
                        turn_id=candidate.turn_id,
                        thread_id=candidate.thread_id,
                        project_id=candidate.project_id,
                        executor=candidate.executor,
                        execution_class=candidate.execution_class,
                        claim=claim,
                    )
                )

        started: list[ExecutionDispatch] = []
        for dispatch in claimed:
            if self._stop.is_set():
                self.release(
                    dispatch.claim,
                    reason="coordinator_quiesced_before_start",
                )
                continue
            with self._lock:
                self._claims[dispatch.turn_id] = dispatch.claim
            try:
                self._start_execution(dispatch)
            except Exception as exc:
                logger.exception(
                    "execution registry rejected admitted Turn %s",
                    dispatch.turn_id,
                )
                if self._on_start_failure is not None:
                    try:
                        self._on_start_failure(dispatch, exc)
                    except Exception:
                        logger.exception(
                            "execution start-failure handler failed for Turn %s",
                            dispatch.turn_id,
                        )
                # Without a successful TurnService settlement the claim remains
                # fenced.  Releasing a still-queued Turn here would cause the
                # background pass to retry it forever.
                continue
            started.append(dispatch)
        return tuple(started)

    def release(
        self,
        claim: ResourceClaim,
        *,
        reason: str,
        released_at: datetime | None = None,
    ) -> bool:
        """Release one claim without allowing a stale receipt to clear its successor."""

        when = released_at or self._now()
        require_aware(when, "released_at")
        with self.database.transaction() as connection:
            released = self.release_in_transaction(
                connection,
                claim,
                reason=reason,
                released_at=when,
            )
        if released:
            self.confirm_released(claim)
        return released

    def release_in_transaction(
        self,
        connection: sqlite3.Connection,
        claim: ResourceClaim,
        *,
        reason: str,
        released_at: datetime | None = None,
    ) -> bool:
        """Release DB ownership inside the caller's terminal Turn transaction.

        Call :meth:`confirm_released` only after that transaction commits.
        """

        when = released_at or self._now()
        require_aware(when, "released_at")
        return RuntimeCoordinationRepository(connection).release_claim(
            claim,
            released_at=when,
            reason=reason,
        )

    def confirm_released(self, claim: ResourceClaim) -> None:
        """Forget a local claim after its database transaction committed."""

        if claim.turn_id is not None:
            with self._lock:
                current = self._claims.get(claim.turn_id)
                if (
                    current is not None
                    and current.worker_id == claim.worker_id
                    and current.turn_epoch == claim.turn_epoch
                ):
                    self._claims.pop(claim.turn_id, None)
        self._wake.set()

    def claim_is_current(self, claim: ResourceClaim) -> bool:
        with self.database.read() as connection:
            return RuntimeCoordinationRepository(connection).claim_is_current(claim)

    def deliver_cancellation_requests(self) -> tuple[ResourceClaim, ...]:
        """Forward durable cancellation only to this claim-owning process."""

        self._require_started()
        requests: list[ExecutionCancellation] = []
        with self.database.read() as connection:
            coordination = RuntimeCoordinationRepository(connection)
            for claim in coordination.list_cancel_requested_claims(self.worker_id):
                executor = coordination.turn_executor_for_claim(claim)
                if executor is not None:
                    requests.append(ExecutionCancellation(executor, claim))
        handler = self._on_cancel_requested
        if handler is not None:
            for request in requests:
                try:
                    handler(request)
                except Exception:
                    logger.exception(
                        "failed to deliver cancellation for Turn %s",
                        request.turn_id,
                    )
        return tuple(request.claim for request in requests)

    def rehome_queued(
        self,
        from_worker_id: str,
        *,
        to_worker_id: str | None = None,
    ) -> tuple[str, ...]:
        """Move only unowned queued work, defaulting to this live worker."""

        self._require_started()
        target = self.worker_id if to_worker_id is None else to_worker_id
        with self.database.transaction() as connection:
            turn_ids = RuntimeCoordinationRepository(
                connection
            ).rehome_unowned_queued_turns(from_worker_id, target)
        if target == self.worker_id and turn_ids:
            self._wake.set()
        return tuple(turn_ids)

    def recover_dead_worker(
        self,
        worker_id: str,
    ) -> DeadWorkerRecovery | None:
        """Recover only after the target worker's OS lock proves it is gone.

        Queued work is safe to release and rehome.  Running or approval-waiting
        Turns retain their leases and are returned for explicit interruption;
        they are never replayed by this coordination layer.
        """

        self._require_started()
        require_prefixed_id(worker_id, "worker")
        if worker_id == self.worker_id:
            return None
        proof = FileLease.acquire(
            self.worker_lock_path(worker_id),
            shared=False,
            blocking=False,
        )
        if proof is None:
            return None
        try:
            now = self._now()
            released_queued: list[str] = []
            released_worker_keys: list[str] = []
            requires_settlement: list[OrphanedExecution] = []
            with self.database.transaction() as connection:
                coordination = RuntimeCoordinationRepository(connection)
                worker = coordination.get_worker(worker_id)
                if worker is None:
                    raise KeyError(worker_id)
                for claim in coordination.list_claims_for_worker(worker_id):
                    release_at = max(
                        now,
                        *(lease.heartbeat_at for lease in claim.leases),
                    )
                    if claim.turn_id is None:
                        if coordination.release_claim(
                            claim,
                            released_at=release_at,
                            reason="worker_crashed",
                        ):
                            released_worker_keys.extend(claim.resource_keys)
                        continue
                    status = coordination.turn_status_for_claim(claim)
                    executor = coordination.turn_executor_for_claim(claim)
                    if executor is None:
                        continue
                    if status == "queued":
                        if coordination.release_claim(
                            claim,
                            released_at=release_at,
                            reason="worker_crashed_before_start",
                        ):
                            released_queued.append(claim.turn_id)
                        continue
                    if status in {"running", "waiting_approval"}:
                        requires_settlement.append(
                            OrphanedExecution(
                                executor=executor,
                                status=status,
                                claim=claim,
                            )
                        )

                rehomed = coordination.rehome_unowned_queued_turns(
                    worker_id,
                    self.worker_id,
                )
                stopped_at = max(now, worker.heartbeat_at)
                if worker.stopped_at is None:
                    coordination.stop_worker(worker_id, stopped_at)
            if rehomed:
                self._wake.set()
            result = DeadWorkerRecovery(
                worker_id=worker_id,
                rehomed_turn_ids=tuple(rehomed),
                released_queued_turn_ids=tuple(released_queued),
                released_worker_resource_keys=tuple(sorted(released_worker_keys)),
                requires_settlement=tuple(requires_settlement),
            )
            handler = self._on_orphaned_execution
            if handler is not None:
                for orphan in result.requires_settlement:
                    try:
                        handler(orphan)
                    except Exception:
                        logger.exception(
                            "failed to settle orphaned Turn %s from worker %s",
                            orphan.turn_id,
                            worker_id,
                        )
            return result
        finally:
            proof.close()

    def recover_candidates(
        self,
        *,
        heartbeat_before: datetime,
    ) -> tuple[DeadWorkerRecovery, ...]:
        """Check stale candidates with OS locks and recover only proven deaths."""

        self._require_started()
        require_aware(heartbeat_before, "heartbeat_before")
        with self.database.read() as connection:
            candidates = RuntimeCoordinationRepository(
                connection
            ).list_recovery_candidates(heartbeat_before=heartbeat_before)
        recovered: list[DeadWorkerRecovery] = []
        for candidate in candidates:
            result = self.recover_dead_worker(candidate.id)
            if result is not None:
                recovered.append(result)
        return tuple(recovered)

    def quiesce(self) -> None:
        """Stop new admission while retaining the worker liveness lock."""

        with self._lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
            if thread.is_alive():
                raise RuntimeError("execution coordinator did not quiesce")
        with self._lock:
            if self._thread is thread:
                self._thread = None
            self._quiesced = True

    def close(self) -> None:
        """Stop the durable worker only after its execution claims are drained."""

        with self._lock:
            if self._closed:
                return
            started = self._worker is not None
        if not started:
            with self._lock:
                self._closed = True
            return
        self.quiesce()

        with self.database.read() as connection:
            held = RuntimeCoordinationRepository(
                connection
            ).list_held_resources_for_worker(self.worker_id)
        if held:
            raise RuntimeError("cannot close a worker with active resource claims")

        now = self._now()
        with self.database.transaction() as connection:
            coordination = RuntimeCoordinationRepository(connection)
            coordination.rehome_unowned_queued_turns(self.worker_id, None)
            worker = coordination.get_worker(self.worker_id)
            if worker is not None and worker.stopped_at is None:
                coordination.stop_worker(
                    self.worker_id,
                    max(now, worker.heartbeat_at),
                )

        with self._lock:
            lease = self._liveness_lease
            self._liveness_lease = None
            self._worker = None
            self._closed = True
        if lease is not None:
            lease.close()

    def _claim_candidate(
        self,
        coordination: RuntimeCoordinationRepository,
        candidate: QueuedTurnCandidate,
        *,
        acquired_at: datetime,
    ) -> ResourceClaim | None:
        scope = ExecutionScope(
            thread_id=candidate.thread_id,
            project_id=candidate.project_id,
            has_managed_worktree=candidate.worktree_path is not None,
        )
        for resource_keys in self._admission_policy.claim_options(
            scope,
            max_concurrent_turns=self.max_concurrent_turns,
        ):
            claim = coordination.claim_turn_resources(
                self.worker_id,
                candidate.turn_id,
                resource_keys,
                acquired_at=acquired_at,
            )
            if claim is not None:
                return claim
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                stale = self.heartbeat()
                if stale:
                    logger.error(
                        "execution coordinator lost %d durable fence(s)",
                        len(stale),
                    )
                self.deliver_cancellation_requests()
                heartbeat_before = self._now() - timedelta(
                    seconds=self.stale_worker_after
                )
                self.recover_candidates(heartbeat_before=heartbeat_before)
                if self._stop.is_set():
                    break
                self.dispatch_once()
            except Exception:
                logger.exception("execution coordinator pass failed")
            self._wake.wait(self.heartbeat_interval)
            self._wake.clear()

    def _start_background_locked(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"deepcode-execution-coordinator-{self.worker_id[-8:]}",
            daemon=True,
        )
        self._thread.start()

    def _require_started(self) -> None:
        with self._lock:
            if self._worker is None or self._closed:
                raise RuntimeError("execution coordinator is not started")

    def _require_dispatchable(self) -> None:
        self._require_started()
        with self._lock:
            if self._quiesced or self._stop.is_set():
                raise RuntimeError("execution coordinator is quiesced")

    def _now(self) -> datetime:
        observed_at = self._clock()
        require_aware(observed_at, "clock result")
        return observed_at


__all__ = [
    "CancellationHandler",
    "DeadWorkerRecovery",
    "ExecutionCoordinator",
    "ExecutionDispatch",
    "OrphanSettlementHandler",
    "OrphanedExecution",
]
