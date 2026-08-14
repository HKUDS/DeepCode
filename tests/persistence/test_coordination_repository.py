from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from core.domain import (
    Project,
    RuntimeWorker,
    Thread,
    ThreadMode,
    Turn,
    TurnExecutor,
)
from core.domain.common import utc_now
from core.persistence import (
    Database,
    ProjectRepository,
    RuntimeCoordinationRepository,
    ThreadRepository,
    TurnRepository,
)
from core.persistence.migrations import current_version, migrate


def _worker(
    suffix: str,
    *,
    heartbeat_at=None,
) -> RuntimeWorker:
    observed_at = heartbeat_at or utc_now()
    return RuntimeWorker(
        id=f"worker_{suffix}",
        pid=1000 + len(suffix),
        surface="test",
        started_at=observed_at,
        heartbeat_at=observed_at,
    )


def _seed_turn(database: Database, tmp_path: Path, *, suffix: str = "claim") -> Turn:
    project = Project(
        canonical_path=str(tmp_path / suffix),
        display_name=f"Project {suffix}",
    )
    thread = Thread(
        project_id=project.id,
        title=f"Thread {suffix}",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
    )
    turn = Turn(
        thread_id=thread.id,
        ordinal=1,
        prompt=f"Coordinate {suffix}",
    )
    with database.transaction() as connection:
        ProjectRepository(connection).add(project)
        ThreadRepository(connection).add(thread)
        TurnRepository(connection).add(turn)
    return turn


def test_v10_migration_is_reversible_and_keeps_legacy_turns_usable(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize(target_version=9)
    legacy = _seed_turn(database, tmp_path, suffix="legacy")

    with database.read() as connection:
        migrate(connection, 10)
        assert current_version(connection) == 10
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"runtime_workers", "resource_leases"} <= tables
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turns)")
        }
        assert {
            "enqueued_at",
            "execution_class",
            "home_worker_id",
            "execution_owner_id",
            "execution_epoch",
            "cancel_requested_at",
        } <= columns
        legacy_row = connection.execute(
            "SELECT enqueued_at, execution_class, execution_owner_id, "
            "execution_epoch FROM turns WHERE id = ?",
            (legacy.id,),
        ).fetchone()
        assert tuple(legacy_row) == (
            "1970-01-01T00:00:00Z",
            "interactive",
            None,
            0,
        )

    current = Turn(
        thread_id=legacy.thread_id,
        ordinal=2,
        prompt="Inserted through the unchanged v9 repository",
    )
    with database.transaction() as connection:
        TurnRepository(connection).add(current)
        worker = _worker("downgrade")
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(worker)
        assert (
            coordination.claim_turn_resources(
                worker.id,
                current.id,
                (f"thread:{current.thread_id}", "capacity:turn:1"),
                acquired_at=worker.started_at,
            )
            is not None
        )
    with database.read() as connection:
        enqueued_at = connection.execute(
            "SELECT enqueued_at FROM turns WHERE id = ?",
            (current.id,),
        ).fetchone()[0]
        assert enqueued_at != "1970-01-01T00:00:00Z"
        migrate(connection, 9)
        assert current_version(connection) == 9
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'runtime_workers'"
            ).fetchone()
            is None
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turns)")
        }
        assert "execution_epoch" not in columns
        restored_legacy = TurnRepository(connection).get(legacy.id)
        restored_current = TurnRepository(connection).get(current.id)
        assert restored_legacy is not None
        assert restored_current is not None
        assert replace(restored_legacy, enqueued_at=legacy.enqueued_at) == legacy
        assert replace(restored_current, enqueued_at=current.enqueued_at) == current


def test_worker_register_heartbeat_liveness_and_stop_are_monotonic(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    started_at = utc_now()
    worker = _worker("lifecycle", heartbeat_at=started_at)

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(worker)
        assert coordination.get_worker(worker.id) == worker

    heartbeat_at = started_at + timedelta(seconds=2)
    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert not coordination.heartbeat_worker(
            worker.id,
            started_at - timedelta(seconds=1),
        )
        assert coordination.heartbeat_worker(worker.id, heartbeat_at)
        assert coordination.list_liveness_candidates(
            heartbeat_before=heartbeat_at,
        ) == [
            RuntimeWorker(
                id=worker.id,
                pid=worker.pid,
                surface=worker.surface,
                started_at=started_at,
                heartbeat_at=heartbeat_at,
            )
        ]

    stopped_at = heartbeat_at + timedelta(seconds=1)
    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert coordination.stop_worker(worker.id, stopped_at)
        assert not coordination.stop_worker(
            worker.id,
            stopped_at + timedelta(seconds=1),
        )
        assert not coordination.heartbeat_worker(
            worker.id,
            stopped_at + timedelta(seconds=2),
        )
        stopped = coordination.get_worker(worker.id)
        assert stopped is not None
        assert stopped.heartbeat_at == stopped_at
        assert stopped.stopped_at == stopped_at
        assert (
            coordination.list_liveness_candidates(
                heartbeat_before=stopped_at + timedelta(days=1),
            )
            == []
        )


def test_non_agent_turn_requires_explicit_worker_affinity_before_admission(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    turn = _seed_turn(database, tmp_path, suffix="workflow-preparation")
    worker = _worker("workflow")

    with database.transaction() as connection:
        turns = TurnRepository(connection)
        staged = replace(turn, executor=TurnExecutor.WORKFLOW)
        connection.execute(
            "UPDATE turns SET executor = ? WHERE id = ?",
            (staged.executor.value, staged.id),
        )
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(worker)
        assert coordination.list_queued_turn_candidates(worker.id) == []

        ready = replace(staged, home_worker_id=worker.id)
        turns.update(ready)
        candidates = coordination.list_queued_turn_candidates(worker.id)
        assert [candidate.turn_id for candidate in candidates] == [turn.id]
        assert candidates[0].executor is TurnExecutor.WORKFLOW


def test_resource_claim_is_all_or_nothing_and_release_is_fenced(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    first = _worker("first", heartbeat_at=now)
    second = _worker("second", heartbeat_at=now)
    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(first)
        coordination.register_worker(second)
        first_claim = coordination.claim_worker_resources(
            first.id,
            ("thread:thread_shared", "capacity:turn:1"),
            acquired_at=now,
        )
        assert first_claim is not None
        assert first_claim.resource_keys == (
            "capacity:turn:1",
            "thread:thread_shared",
        )

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert (
            coordination.claim_worker_resources(
                second.id,
                ("workspace:project:free", "capacity:turn:1"),
                acquired_at=now + timedelta(seconds=1),
            )
            is None
        )
        assert coordination.get_resource_lease("workspace:project:free") is None
        assert coordination.heartbeat_claim(
            first_claim,
            observed_at=now + timedelta(seconds=2),
        )
        assert coordination.release_claim(
            first_claim,
            released_at=now + timedelta(seconds=3),
            reason="completed",
        )

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        second_claim = coordination.claim_worker_resources(
            second.id,
            ("thread:thread_shared", "capacity:turn:1"),
            acquired_at=now + timedelta(seconds=4),
        )
        assert second_claim is not None
        assert {lease.epoch for lease in second_claim.leases} == {2}
        assert not coordination.release_claim(
            first_claim,
            released_at=now + timedelta(seconds=5),
            reason="stale_owner",
        )
        assert {
            lease.holder_worker_id
            for lease in coordination.list_held_resources_for_worker(second.id)
        } == {second.id}
        assert coordination.release_claim(
            second_claim,
            released_at=now + timedelta(seconds=5),
            reason="completed",
        )
        released = coordination.get_resource_lease("capacity:turn:1")
        assert released is not None
        assert not released.held
        assert released.epoch == 2
        assert released.release_reason == "completed"


def test_turn_claim_respects_home_worker_and_rejects_stale_epoch(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    turn = _seed_turn(database, tmp_path)
    now = utc_now()
    first = _worker("turn_first", heartbeat_at=now)
    second = _worker("turn_second", heartbeat_at=now)
    keys = (f"thread:{turn.thread_id}", "workspace:project:claim")

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        coordination.register_worker(first)
        coordination.register_worker(second)
        connection.execute(
            "UPDATE turns SET home_worker_id = ?, "
            "execution_class = 'manual_automation' WHERE id = ?",
            (first.id, turn.id),
        )
        assert (
            coordination.claim_turn_resources(
                second.id,
                turn.id,
                keys,
                acquired_at=now,
            )
            is None
        )
        first_claim = coordination.claim_turn_resources(
            first.id,
            turn.id,
            keys,
            acquired_at=now,
        )
        assert first_claim is not None
        assert first_claim.turn_epoch == 1
        owner = connection.execute(
            "SELECT execution_owner_id, execution_epoch FROM turns WHERE id = ?",
            (turn.id,),
        ).fetchone()
        assert tuple(owner) == (first.id, 1)

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        assert coordination.release_claim(
            first_claim,
            released_at=now + timedelta(seconds=1),
            reason="rehome",
        )
        connection.execute(
            "UPDATE turns SET home_worker_id = ? WHERE id = ?",
            (second.id, turn.id),
        )
        second_claim = coordination.claim_turn_resources(
            second.id,
            turn.id,
            keys,
            acquired_at=now + timedelta(seconds=2),
        )
        assert second_claim is not None
        assert second_claim.turn_epoch == 2
        assert not coordination.heartbeat_claim(
            first_claim,
            observed_at=now + timedelta(seconds=3),
        )
        assert not coordination.release_claim(
            first_claim,
            released_at=now + timedelta(seconds=3),
            reason="stale_owner",
        )
        owner = connection.execute(
            "SELECT execution_owner_id, execution_epoch FROM turns WHERE id = ?",
            (turn.id,),
        ).fetchone()
        assert tuple(owner) == (second.id, 2)


def test_two_connections_have_one_atomic_resource_claim_winner(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    workers = (_worker("race_a", heartbeat_at=now), _worker("race_b", heartbeat_at=now))
    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        for worker in workers:
            coordination.register_worker(worker)

    barrier = threading.Barrier(2)

    def contend(worker: RuntimeWorker):
        barrier.wait(timeout=3)
        with database.transaction() as connection:
            return RuntimeCoordinationRepository(connection).claim_worker_resources(
                worker.id,
                ("scheduler:automation", "capacity:turn:1"),
                acquired_at=now + timedelta(seconds=1),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(contend, workers))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    with database.read() as connection:
        leases = RuntimeCoordinationRepository(
            connection
        ).list_held_resources_for_worker(winners[0].worker_id)
        assert [lease.resource_key for lease in leases] == [
            "capacity:turn:1",
            "scheduler:automation",
        ]


def test_recovery_query_returns_only_stale_or_stopped_resource_owners(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    now = utc_now()
    stale = _worker("stale", heartbeat_at=now - timedelta(minutes=5))
    fresh = _worker("fresh", heartbeat_at=now)
    stopped = _worker("stopped", heartbeat_at=now - timedelta(minutes=1))

    with database.transaction() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        for worker in (stale, fresh, stopped):
            coordination.register_worker(worker)
            assert (
                coordination.claim_worker_resources(
                    worker.id,
                    (f"worker-liveness:{worker.id}",),
                    acquired_at=worker.heartbeat_at,
                )
                is not None
            )
        assert coordination.stop_worker(stopped.id, now)

    with database.read() as connection:
        coordination = RuntimeCoordinationRepository(connection)
        cutoff = now - timedelta(minutes=2)
        assert [
            worker.id
            for worker in coordination.list_liveness_candidates(heartbeat_before=cutoff)
        ] == [stale.id]
        assert {
            worker.id
            for worker in coordination.list_recovery_candidates(heartbeat_before=cutoff)
        } == {stale.id, stopped.id}
        assert [
            lease.resource_key
            for lease in coordination.list_held_resources_for_worker(stale.id)
        ] == [f"worker-liveness:{stale.id}"]
