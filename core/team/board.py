"""TaskBoard — a durable, dependency-aware work-stealing board (P4).

The SprintContract of the team: a lead writes tasks (each with a spec, a
verification command, and dependencies); workers repeatedly *claim* a ready
task, do it, and *complete* it, which unblocks dependents. Backed by SQLite so
it is durable, inspectable, and — the load-bearing property — **claims are
atomic**: two workers can never grab the same task (§8 anti-pattern #9, the
claim race). A stale claim from a dead worker is reclaimable.

This is pure coordination state: no agent, no LLM, no subprocess. The
concurrency it guards is real — N workers claim concurrently — so the atomic
``UPDATE ... WHERE status='ready'`` + affected-row check is the mechanism, not
a lock held across the actual work.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Task lifecycle.
PENDING = "pending"  # created; some dependency is not yet done
READY = "ready"  # all dependencies done; claimable
RUNNING = "running"  # claimed by a worker
DONE = "done"  # completed successfully
FAILED = "failed"  # worker gave up / verification failed
_TERMINAL = {DONE, FAILED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    spec          TEXT NOT NULL DEFAULT '',
    test_command  TEXT NOT NULL DEFAULT '',
    deps          TEXT NOT NULL DEFAULT '[]',   -- JSON array of task ids
    status        TEXT NOT NULL DEFAULT 'pending',
    assignee      TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    claimed_at    REAL NOT NULL DEFAULT 0,
    seq           INTEGER
);
"""


@dataclass
class Task:
    id: str
    title: str = ""
    spec: str = ""
    test_command: str = ""
    deps: list[str] = field(default_factory=list)
    status: str = PENDING
    assignee: str = ""
    result: str = ""


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        spec=row["spec"],
        test_command=row["test_command"],
        deps=json.loads(row["deps"] or "[]"),
        status=row["status"],
        assignee=row["assignee"],
        result=row["result"],
    )


class CycleError(ValueError):
    """The task graph has a dependency cycle (it could never drain)."""


class TaskBoard:
    """A durable dependency-aware board with atomic claims."""

    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # IMMEDIATE transactions so a claim takes the write lock up front —
        # concurrent claimers serialise instead of racing to UPDATE.
        self._conn.isolation_level = None  # explicit BEGIN control
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._seq = 0

    # -- authoring -------------------------------------------------------------

    def add_task(
        self,
        task_id: str,
        *,
        title: str = "",
        spec: str = "",
        test_command: str = "",
        deps: list[str] | None = None,
    ) -> Task:
        deps = list(deps or [])
        status = READY if not deps else PENDING
        with self._lock:
            self._seq += 1
            self._conn.execute(
                "INSERT OR REPLACE INTO tasks "
                "(id, title, spec, test_command, deps, status, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    title,
                    spec,
                    test_command,
                    json.dumps(deps),
                    status,
                    self._seq,
                ),
            )
            self._conn.commit()
        return Task(task_id, title, spec, test_command, deps, status)

    def validate(self) -> None:
        """Raise :class:`CycleError` if the dependency graph has a cycle, or a
        dependency references a task that does not exist."""
        tasks = {t.id: t for t in self.all_tasks()}
        for t in tasks.values():
            for dep in t.deps:
                if dep not in tasks:
                    raise CycleError(f"task {t.id!r} depends on unknown task {dep!r}")
        # DFS cycle check.
        WHITE, GREY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in tasks}

        def visit(tid: str) -> None:
            color[tid] = GREY
            for dep in tasks[tid].deps:
                if color[dep] == GREY:
                    raise CycleError(f"dependency cycle involving {tid!r} -> {dep!r}")
                if color[dep] == WHITE:
                    visit(dep)
            color[tid] = BLACK

        for tid in tasks:
            if color[tid] == WHITE:
                visit(tid)

    # -- worker protocol -------------------------------------------------------

    def claim(self, worker_id: str) -> Task | None:
        """Atomically claim one ready task for ``worker_id``, or ``None``.

        A task is claimable when it is ``ready`` (all deps done). The
        ``UPDATE ... WHERE status='ready'`` guard + affected-row check makes
        the claim safe against concurrent claimers.
        """
        with self._lock:
            self._promote_ready()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY seq LIMIT 1",
                    (READY,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                cur = self._conn.execute(
                    "UPDATE tasks SET status=?, assignee=?, claimed_at=? "
                    "WHERE id=? AND status=?",
                    (RUNNING, worker_id, time.time(), row["id"], READY),
                )
                if cur.rowcount != 1:  # someone else won the race
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            claimed = _row_to_task(row)
            claimed.status = RUNNING
            claimed.assignee = worker_id
            return claimed

    def complete(self, task_id: str, *, success: bool, result: str = "") -> None:
        """Mark a task done/failed and record its result; unblock dependents."""
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status=?, result=? WHERE id=?",
                (DONE if success else FAILED, result, task_id),
            )
            self._conn.commit()
            self._promote_ready()

    def reclaim_stale(self, timeout: float) -> int:
        """Return long-running claims to ``ready`` (dead-worker reclaim).

        Returns how many were reclaimed.
        """
        cutoff = time.time() - timeout
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status=?, assignee='' "
                "WHERE status=? AND claimed_at < ?",
                (READY, RUNNING, cutoff),
            )
            self._conn.commit()
            return cur.rowcount

    # -- queries ---------------------------------------------------------------

    def _promote_ready(self) -> None:
        """Advance pending tasks: ready when all deps are done, failed when any
        dep failed (a task can never run once a prerequisite is dead — cascade
        so the board always settles instead of hanging on it)."""
        done, failed = set(), set()
        for r in self._conn.execute(
            "SELECT id, status FROM tasks WHERE status IN (?, ?)", (DONE, FAILED)
        ).fetchall():
            (done if r["status"] == DONE else failed).add(r["id"])
        for row in self._conn.execute(
            "SELECT id, deps FROM tasks WHERE status=?", (PENDING,)
        ).fetchall():
            deps = json.loads(row["deps"] or "[]")
            if any(d in failed for d in deps):
                self._conn.execute(
                    "UPDATE tasks SET status=?, result=? WHERE id=? AND status=?",
                    (FAILED, "blocked: a dependency failed", row["id"], PENDING),
                )
            elif all(d in done for d in deps):
                self._conn.execute(
                    "UPDATE tasks SET status=? WHERE id=? AND status=?",
                    (READY, row["id"], PENDING),
                )
        self._conn.commit()

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            return _row_to_task(row) if row else None

    def all_tasks(self) -> list[Task]:
        with self._lock:
            return [
                _row_to_task(r)
                for r in self._conn.execute(
                    "SELECT * FROM tasks ORDER BY seq"
                ).fetchall()
            ]

    def counts(self) -> dict[str, int]:
        with self._lock:
            out = {PENDING: 0, READY: 0, RUNNING: 0, DONE: 0, FAILED: 0}
            for r in self._conn.execute(
                "SELECT status, COUNT(*) c FROM tasks GROUP BY status"
            ).fetchall():
                out[r["status"]] = r["c"]
            return out

    def all_settled(self) -> bool:
        """True when every task is in a terminal state (done or failed)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM tasks WHERE status NOT IN (?, ?)",
                (DONE, FAILED),
            ).fetchone()
            return row["c"] == 0

    def all_succeeded(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM tasks WHERE status != ?", (DONE,)
            ).fetchone()
            total = self._conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()
            return total["c"] > 0 and row["c"] == 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
