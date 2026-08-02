"""Persistence operations for cross-process runtime coordination."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from core.domain.common import (
    require_aware,
    require_non_empty,
    require_prefixed_id,
)
from core.domain.runtime_coordination import (
    ExecutionClass,
    ResourceClaim,
    ResourceLease,
    RuntimeWorker,
)
from core.domain.turn import TurnExecutor
from core.persistence.serde import (
    dump_datetime,
    load_datetime,
    load_required_datetime,
)


@dataclass(frozen=True, slots=True)
class QueuedTurnCandidate:
    """Minimal durable projection needed by the execution coordinator."""

    turn_id: str
    thread_id: str
    project_id: str
    worktree_path: str | None
    executor: TurnExecutor
    execution_class: ExecutionClass
    enqueued_at: datetime
    ordinal: int


class RuntimeCoordinationRepository:
    """Coordinate workers and fenced resources inside one SQLite boundary.

    Multi-row claim, heartbeat, and release methods require the repository's
    connection to already be inside a write transaction.  ``Database.transaction``
    provides the expected ``BEGIN IMMEDIATE`` boundary.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def register_worker(self, worker: RuntimeWorker) -> None:
        self.connection.execute(
            "INSERT INTO runtime_workers ("
            "id, pid, surface, started_at, heartbeat_at, stopped_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                worker.id,
                worker.pid,
                worker.surface,
                dump_datetime(worker.started_at),
                dump_datetime(worker.heartbeat_at),
                dump_datetime(worker.stopped_at),
            ),
        )

    def get_worker(self, worker_id: str) -> RuntimeWorker | None:
        require_prefixed_id(worker_id, "worker")
        row = self.connection.execute(
            "SELECT * FROM runtime_workers WHERE id = ?",
            (worker_id,),
        ).fetchone()
        return self._worker_from_row(row) if row is not None else None

    def heartbeat_worker(self, worker_id: str, observed_at: datetime) -> bool:
        require_prefixed_id(worker_id, "worker")
        require_aware(observed_at, "observed_at")
        encoded = dump_datetime(observed_at)
        cursor = self.connection.execute(
            "UPDATE runtime_workers SET heartbeat_at = ? "
            "WHERE id = ? AND stopped_at IS NULL AND heartbeat_at <= ?",
            (encoded, worker_id, encoded),
        )
        return cursor.rowcount == 1

    def stop_worker(self, worker_id: str, stopped_at: datetime) -> bool:
        require_prefixed_id(worker_id, "worker")
        require_aware(stopped_at, "stopped_at")
        encoded = dump_datetime(stopped_at)
        cursor = self.connection.execute(
            "UPDATE runtime_workers SET heartbeat_at = ?, stopped_at = ? "
            "WHERE id = ? AND stopped_at IS NULL AND heartbeat_at <= ?",
            (encoded, encoded, worker_id, encoded),
        )
        return cursor.rowcount == 1

    def list_liveness_candidates(
        self,
        *,
        heartbeat_before: datetime,
    ) -> list[RuntimeWorker]:
        """Return active workers whose OS liveness lock should be checked."""

        require_aware(heartbeat_before, "heartbeat_before")
        rows = self.connection.execute(
            "SELECT * FROM runtime_workers "
            "WHERE stopped_at IS NULL AND heartbeat_at <= ? "
            "ORDER BY heartbeat_at, id",
            (dump_datetime(heartbeat_before),),
        ).fetchall()
        return [self._worker_from_row(row) for row in rows]

    def list_recovery_candidates(
        self,
        *,
        heartbeat_before: datetime,
    ) -> list[RuntimeWorker]:
        """Return stale/stopped workers that still own recoverable work.

        A stale heartbeat is only a candidate signal.  Callers must prove death
        with the worker's OS liveness lock before mutating its claims.
        """

        require_aware(heartbeat_before, "heartbeat_before")
        rows = self.connection.execute(
            "SELECT runtime_workers.* FROM runtime_workers "
            "WHERE ("
            "    EXISTS ("
            "        SELECT 1 FROM resource_leases "
            "        WHERE resource_leases.holder_worker_id = runtime_workers.id"
            "    ) OR EXISTS ("
            "        SELECT 1 FROM turns "
            "        WHERE turns.home_worker_id = runtime_workers.id "
            "        AND turns.status = 'queued' "
            "        AND turns.execution_owner_id IS NULL"
            "    )"
            ") AND ("
            "    runtime_workers.stopped_at IS NOT NULL OR "
            "    runtime_workers.heartbeat_at <= ?"
            ") "
            "ORDER BY "
            "CASE WHEN runtime_workers.stopped_at IS NOT NULL THEN 0 ELSE 1 END, "
            "runtime_workers.heartbeat_at, runtime_workers.id",
            (dump_datetime(heartbeat_before),),
        ).fetchall()
        return [self._worker_from_row(row) for row in rows]

    def list_queued_turn_candidates(
        self,
        worker_id: str,
        *,
        limit: int = 128,
    ) -> list[QueuedTurnCandidate]:
        """List eligible Thread heads for one worker in durable FIFO order."""

        require_prefixed_id(worker_id, "worker")
        if isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        executor_available = self._turn_executor_available()
        executor_projection = (
            "turns.executor" if executor_available else "'agent' AS executor"
        )
        if executor_available:
            # Agent Turns may be submitted without worker affinity.  Every
            # other typed executor must explicitly assign a live home worker
            # before admission, which also gives those executors a durable
            # preparation boundary.
            worker_scope = (
                "AND ((turns.executor = ? AND ("
                "turns.home_worker_id IS NULL OR turns.home_worker_id = ?"
                ")) OR (turns.executor <> ? AND turns.home_worker_id = ?)) "
            )
            parameters: tuple[object, ...] = (
                TurnExecutor.AGENT.value,
                worker_id,
                TurnExecutor.AGENT.value,
                worker_id,
                limit,
            )
        else:
            worker_scope = (
                "AND (turns.home_worker_id IS NULL OR turns.home_worker_id = ?) "
            )
            parameters = (worker_id, limit)
        rows = self.connection.execute(
            "SELECT "
            "turns.id AS turn_id, turns.thread_id, threads.project_id, "
            f"threads.worktree_path, {executor_projection}, "
            "turns.execution_class, "
            "turns.enqueued_at, turns.ordinal "
            "FROM turns JOIN threads ON threads.id = turns.thread_id "
            "WHERE turns.status = 'queued' "
            "AND turns.execution_owner_id IS NULL "
            "AND turns.cancel_requested_at IS NULL "
            "AND threads.status <> 'archived' "
            f"{worker_scope}"
            "AND NOT EXISTS ("
            "    SELECT 1 FROM turns AS predecessor "
            "    WHERE predecessor.thread_id = turns.thread_id "
            "    AND predecessor.ordinal < turns.ordinal "
            "    AND predecessor.status IN "
            "        ('queued', 'running', 'waiting_approval')"
            ") "
            "ORDER BY turns.enqueued_at, turns.thread_id, turns.ordinal, turns.id "
            "LIMIT ?",
            parameters,
        ).fetchall()
        return [
            QueuedTurnCandidate(
                turn_id=row["turn_id"],
                thread_id=row["thread_id"],
                project_id=row["project_id"],
                worktree_path=row["worktree_path"],
                executor=TurnExecutor(row["executor"]),
                execution_class=ExecutionClass(row["execution_class"]),
                enqueued_at=load_required_datetime(row["enqueued_at"]),
                ordinal=row["ordinal"],
            )
            for row in rows
        ]

    def turn_executor_for_claim(
        self,
        claim: ResourceClaim,
    ) -> TurnExecutor | None:
        """Return the typed executor only while the complete claim is current."""

        if claim.turn_id is None or not self.claim_is_current(claim):
            return None
        projection = (
            "executor" if self._turn_executor_available() else "'agent' AS executor"
        )
        row = self.connection.execute(
            f"SELECT {projection} FROM turns WHERE id = ?",
            (claim.turn_id,),
        ).fetchone()
        return TurnExecutor(row["executor"]) if row is not None else None

    def current_claim_for_turn(self, turn_id: str) -> ResourceClaim | None:
        """Rebuild the current durable claim for a Turn, if it is fully owned."""

        require_prefixed_id(turn_id, "turn")
        row = self.connection.execute(
            "SELECT execution_owner_id, execution_epoch FROM turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        if (
            row is None
            or row["execution_owner_id"] is None
            or int(row["execution_epoch"]) < 1
        ):
            return None
        worker_id = str(row["execution_owner_id"])
        turn_epoch = int(row["execution_epoch"])
        rows = self.connection.execute(
            "SELECT * FROM resource_leases "
            "WHERE holder_worker_id = ? AND holder_turn_id = ? "
            "AND holder_turn_epoch = ? ORDER BY resource_key",
            (worker_id, turn_id, turn_epoch),
        ).fetchall()
        if not rows:
            return None
        claim = ResourceClaim(
            worker_id=worker_id,
            turn_id=turn_id,
            turn_epoch=turn_epoch,
            leases=tuple(self._lease_from_row(current) for current in rows),
        )
        return claim if self.claim_is_current(claim) else None

    def claim_worker_resources(
        self,
        worker_id: str,
        resource_keys: Iterable[str],
        *,
        acquired_at: datetime,
    ) -> ResourceClaim | None:
        """Atomically claim resources that belong to a worker, such as leadership."""

        return self._claim_resources(
            worker_id,
            resource_keys,
            acquired_at=acquired_at,
            turn_id=None,
        )

    def claim_turn_resources(
        self,
        worker_id: str,
        turn_id: str,
        resource_keys: Iterable[str],
        *,
        acquired_at: datetime,
    ) -> ResourceClaim | None:
        """Atomically fence a queued Turn and all resources it needs."""

        require_prefixed_id(turn_id, "turn")
        return self._claim_resources(
            worker_id,
            resource_keys,
            acquired_at=acquired_at,
            turn_id=turn_id,
        )

    def get_resource_lease(self, resource_key: str) -> ResourceLease | None:
        self._validate_resource_key(resource_key)
        row = self.connection.execute(
            "SELECT * FROM resource_leases WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()
        return self._lease_from_row(row) if row is not None else None

    def list_held_resources_for_worker(
        self,
        worker_id: str,
    ) -> list[ResourceLease]:
        require_prefixed_id(worker_id, "worker")
        rows = self.connection.execute(
            "SELECT * FROM resource_leases "
            "WHERE holder_worker_id = ? ORDER BY resource_key",
            (worker_id,),
        ).fetchall()
        return [self._lease_from_row(row) for row in rows]

    def list_claims_for_worker(self, worker_id: str) -> list[ResourceClaim]:
        """Rebuild active fencing receipts for recovery after proven worker death."""

        leases = self.list_held_resources_for_worker(worker_id)
        turn_groups: dict[tuple[str, int], list[ResourceLease]] = {}
        claims: list[ResourceClaim] = []
        for lease in leases:
            if lease.holder_turn_id is None:
                claims.append(ResourceClaim(worker_id=worker_id, leases=(lease,)))
                continue
            assert lease.holder_turn_epoch is not None
            turn_groups.setdefault(
                (lease.holder_turn_id, lease.holder_turn_epoch),
                [],
            ).append(lease)
        for (turn_id, turn_epoch), grouped in turn_groups.items():
            claims.append(
                ResourceClaim(
                    worker_id=worker_id,
                    turn_id=turn_id,
                    turn_epoch=turn_epoch,
                    leases=tuple(grouped),
                )
            )
        return sorted(
            claims,
            key=lambda claim: (
                claim.turn_id is not None,
                claim.turn_id or "",
                claim.turn_epoch or 0,
                claim.resource_keys,
            ),
        )

    def list_cancel_requested_claims(
        self,
        worker_id: str,
    ) -> list[ResourceClaim]:
        """Return this worker's fenced Turns with a durable cancel request."""

        requested: list[ResourceClaim] = []
        for claim in self.list_claims_for_worker(worker_id):
            if claim.turn_id is None:
                continue
            row = self.connection.execute(
                "SELECT cancel_requested_at FROM turns WHERE id = ?",
                (claim.turn_id,),
            ).fetchone()
            if row is not None and row["cancel_requested_at"] is not None:
                requested.append(claim)
        return requested

    def claim_is_current(self, claim: ResourceClaim) -> bool:
        """Check the complete resource and Turn fence without refreshing it."""

        if self._matching_claim_rows(claim) is None:
            return False
        if claim.turn_id is None:
            return True
        row = self.connection.execute(
            "SELECT execution_owner_id, execution_epoch FROM turns WHERE id = ?",
            (claim.turn_id,),
        ).fetchone()
        return bool(
            row is not None
            and row["execution_owner_id"] == claim.worker_id
            and row["execution_epoch"] == claim.turn_epoch
        )

    def turn_status_for_claim(self, claim: ResourceClaim) -> str | None:
        if claim.turn_id is None or not self.claim_is_current(claim):
            return None
        row = self.connection.execute(
            "SELECT status FROM turns WHERE id = ?",
            (claim.turn_id,),
        ).fetchone()
        return str(row["status"]) if row is not None else None

    def rehome_unowned_queued_turns(
        self,
        from_worker_id: str,
        to_worker_id: str | None,
    ) -> list[str]:
        """Move only unclaimed queued work; live or fenced work is untouched."""

        self._require_write_transaction()
        require_prefixed_id(from_worker_id, "worker")
        if to_worker_id is not None:
            require_prefixed_id(to_worker_id, "worker")
            target = self.connection.execute(
                "SELECT 1 FROM runtime_workers WHERE id = ? AND stopped_at IS NULL",
                (to_worker_id,),
            ).fetchone()
            if target is None:
                raise ValueError("rehome target must be an active worker")
        rows = self.connection.execute(
            "SELECT id FROM turns "
            "WHERE status = 'queued' AND execution_owner_id IS NULL "
            "AND home_worker_id = ? ORDER BY enqueued_at, thread_id, ordinal, id",
            (from_worker_id,),
        ).fetchall()
        turn_ids = [str(row["id"]) for row in rows]
        if turn_ids:
            self.connection.execute(
                "UPDATE turns SET home_worker_id = ? "
                "WHERE status = 'queued' AND execution_owner_id IS NULL "
                "AND home_worker_id = ?",
                (to_worker_id, from_worker_id),
            )
        return turn_ids

    def heartbeat_claim(
        self,
        claim: ResourceClaim,
        *,
        observed_at: datetime,
    ) -> bool:
        """Advance every lease heartbeat only if the full fencing receipt matches."""

        self._require_write_transaction()
        require_aware(observed_at, "observed_at")
        current = self._matching_claim_rows(claim)
        if current is None:
            return False
        if any(observed_at < lease.heartbeat_at for lease in current):
            return False
        encoded = dump_datetime(observed_at)
        for lease in claim.leases:
            cursor = self.connection.execute(
                "UPDATE resource_leases SET heartbeat_at = ? "
                "WHERE resource_key = ? AND epoch = ? "
                "AND holder_worker_id = ? "
                "AND holder_turn_id IS ? AND holder_turn_epoch IS ?",
                (
                    encoded,
                    lease.resource_key,
                    lease.epoch,
                    claim.worker_id,
                    claim.turn_id,
                    claim.turn_epoch,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - write lock prevents drift
                raise sqlite3.IntegrityError("resource claim changed during heartbeat")
        return True

    def release_claim(
        self,
        claim: ResourceClaim,
        *,
        released_at: datetime,
        reason: str,
    ) -> bool:
        """Release the complete claim if its worker, Turn, and epochs still match."""

        self._require_write_transaction()
        require_aware(released_at, "released_at")
        require_non_empty(reason, "reason")
        current = self._matching_claim_rows(claim)
        if current is None:
            return False
        if any(released_at < lease.heartbeat_at for lease in current):
            raise ValueError("resource release cannot precede its heartbeat")

        if claim.turn_id is not None:
            owner = self.connection.execute(
                "SELECT execution_owner_id, execution_epoch FROM turns WHERE id = ?",
                (claim.turn_id,),
            ).fetchone()
            if (
                owner is None
                or owner["execution_owner_id"] != claim.worker_id
                or owner["execution_epoch"] != claim.turn_epoch
            ):
                return False
            held_count = self.connection.execute(
                "SELECT COUNT(*) FROM resource_leases "
                "WHERE holder_worker_id = ? AND holder_turn_id = ? "
                "AND holder_turn_epoch = ?",
                (claim.worker_id, claim.turn_id, claim.turn_epoch),
            ).fetchone()[0]
            if held_count != len(claim.leases):
                return False

        encoded = dump_datetime(released_at)
        for lease in claim.leases:
            cursor = self.connection.execute(
                "UPDATE resource_leases SET "
                "holder_worker_id = NULL, holder_turn_id = NULL, "
                "holder_turn_epoch = NULL, released_at = ?, release_reason = ? "
                "WHERE resource_key = ? AND epoch = ? "
                "AND holder_worker_id = ? "
                "AND holder_turn_id IS ? AND holder_turn_epoch IS ?",
                (
                    encoded,
                    reason,
                    lease.resource_key,
                    lease.epoch,
                    claim.worker_id,
                    claim.turn_id,
                    claim.turn_epoch,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - write lock prevents drift
                raise sqlite3.IntegrityError("resource claim changed during release")

        if claim.turn_id is not None:
            cursor = self.connection.execute(
                "UPDATE turns SET execution_owner_id = NULL "
                "WHERE id = ? AND execution_owner_id = ? AND execution_epoch = ?",
                (claim.turn_id, claim.worker_id, claim.turn_epoch),
            )
            if cursor.rowcount != 1:  # pragma: no cover - write lock prevents drift
                raise sqlite3.IntegrityError("Turn claim changed during release")
        return True

    def _claim_resources(
        self,
        worker_id: str,
        resource_keys: Iterable[str],
        *,
        acquired_at: datetime,
        turn_id: str | None,
    ) -> ResourceClaim | None:
        self._require_write_transaction()
        require_prefixed_id(worker_id, "worker")
        require_aware(acquired_at, "acquired_at")
        keys = self._normalize_resource_keys(resource_keys)
        encoded = dump_datetime(acquired_at)

        worker = self.connection.execute(
            "SELECT 1 FROM runtime_workers "
            "WHERE id = ? AND stopped_at IS NULL AND started_at <= ?",
            (worker_id, encoded),
        ).fetchone()
        if worker is None:
            return None

        placeholders = ", ".join("?" for _ in keys)
        busy = self.connection.execute(
            "SELECT 1 FROM resource_leases "
            f"WHERE resource_key IN ({placeholders}) "
            "AND holder_worker_id IS NOT NULL LIMIT 1",
            keys,
        ).fetchone()
        if busy is not None:
            return None

        turn_epoch: int | None = None
        if turn_id is not None:
            turn = self.connection.execute(
                "SELECT status, home_worker_id, execution_owner_id, "
                "execution_epoch, cancel_requested_at "
                "FROM turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            if (
                turn is None
                or turn["status"] != "queued"
                or turn["execution_owner_id"] is not None
                or turn["cancel_requested_at"] is not None
                or (
                    turn["home_worker_id"] is not None
                    and turn["home_worker_id"] != worker_id
                )
            ):
                return None
            previous_epoch = int(turn["execution_epoch"])
            turn_epoch = previous_epoch + 1
            cursor = self.connection.execute(
                "UPDATE turns SET execution_owner_id = ?, execution_epoch = ? "
                "WHERE id = ? AND status = 'queued' "
                "AND execution_owner_id IS NULL AND execution_epoch = ? "
                "AND cancel_requested_at IS NULL "
                "AND (home_worker_id IS NULL OR home_worker_id = ?)",
                (
                    worker_id,
                    turn_epoch,
                    turn_id,
                    previous_epoch,
                    worker_id,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - write lock prevents drift
                return None

        for key in keys:
            cursor = self.connection.execute(
                "INSERT INTO resource_leases ("
                "resource_key, epoch, holder_worker_id, holder_turn_id, "
                "holder_turn_epoch, acquired_at, heartbeat_at, "
                "released_at, release_reason"
                ") VALUES (?, 1, ?, ?, ?, ?, ?, NULL, NULL) "
                "ON CONFLICT(resource_key) DO UPDATE SET "
                "epoch = resource_leases.epoch + 1, "
                "holder_worker_id = excluded.holder_worker_id, "
                "holder_turn_id = excluded.holder_turn_id, "
                "holder_turn_epoch = excluded.holder_turn_epoch, "
                "acquired_at = excluded.acquired_at, "
                "heartbeat_at = excluded.heartbeat_at, "
                "released_at = NULL, release_reason = NULL "
                "WHERE resource_leases.holder_worker_id IS NULL",
                (
                    key,
                    worker_id,
                    turn_id,
                    turn_epoch,
                    encoded,
                    encoded,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - preflight owns write lock
                raise sqlite3.IntegrityError("resource became busy during claim")

        rows = self.connection.execute(
            "SELECT * FROM resource_leases "
            f"WHERE resource_key IN ({placeholders}) ORDER BY resource_key",
            keys,
        ).fetchall()
        leases = tuple(self._lease_from_row(row) for row in rows)
        return ResourceClaim(
            worker_id=worker_id,
            turn_id=turn_id,
            turn_epoch=turn_epoch,
            leases=leases,
        )

    def _matching_claim_rows(
        self,
        claim: ResourceClaim,
    ) -> tuple[ResourceLease, ...] | None:
        keys = claim.resource_keys
        placeholders = ", ".join("?" for _ in keys)
        rows = self.connection.execute(
            "SELECT * FROM resource_leases "
            f"WHERE resource_key IN ({placeholders}) ORDER BY resource_key",
            keys,
        ).fetchall()
        if len(rows) != len(claim.leases):
            return None
        current = tuple(self._lease_from_row(row) for row in rows)
        expected = {lease.resource_key: lease for lease in claim.leases}
        for lease in current:
            original = expected.get(lease.resource_key)
            if (
                original is None
                or lease.epoch != original.epoch
                or lease.holder_worker_id != claim.worker_id
                or lease.holder_turn_id != claim.turn_id
                or lease.holder_turn_epoch != claim.turn_epoch
            ):
                return None
        return current

    def _require_write_transaction(self) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError("resource coordination requires a write transaction")

    def _turn_executor_available(self) -> bool:
        return any(
            row["name"] == "executor"
            for row in self.connection.execute("PRAGMA table_info(turns)")
        )

    @classmethod
    def _normalize_resource_keys(
        cls,
        resource_keys: Iterable[str],
    ) -> tuple[str, ...]:
        keys = tuple(resource_keys)
        if not keys:
            raise ValueError("at least one resource key is required")
        for key in keys:
            cls._validate_resource_key(key)
        if len(set(keys)) != len(keys):
            raise ValueError("resource keys must be unique")
        return tuple(sorted(keys))

    @staticmethod
    def _validate_resource_key(resource_key: str) -> None:
        require_non_empty(resource_key, "resource_key")
        if len(resource_key) > 512:
            raise ValueError("resource_key cannot exceed 512 characters")

    @staticmethod
    def _worker_from_row(row: sqlite3.Row) -> RuntimeWorker:
        return RuntimeWorker(
            id=row["id"],
            pid=row["pid"],
            surface=row["surface"],
            started_at=load_required_datetime(row["started_at"]),
            heartbeat_at=load_required_datetime(row["heartbeat_at"]),
            stopped_at=load_datetime(row["stopped_at"]),
        )

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> ResourceLease:
        return ResourceLease(
            resource_key=row["resource_key"],
            epoch=row["epoch"],
            holder_worker_id=row["holder_worker_id"],
            holder_turn_id=row["holder_turn_id"],
            holder_turn_epoch=row["holder_turn_epoch"],
            acquired_at=load_required_datetime(row["acquired_at"]),
            heartbeat_at=load_required_datetime(row["heartbeat_at"]),
            released_at=load_datetime(row["released_at"]),
            release_reason=row["release_reason"],
        )


__all__ = ["QueuedTurnCandidate", "RuntimeCoordinationRepository"]
