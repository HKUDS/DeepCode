"""SQLite index over the JSONL session store (P2, L2).

The JSONL files under ``<root>/<session_id>/`` remain the single source of
truth (human-readable, ``tail -f``-able, survive a corrupt index). This is a
*derived* index that answers the store's list/lookup queries without the
O(N) full-session reads they used to need:

* ``list_sessions`` re-read every session's metadata header and line-counted
  two files per session;
* ``find_session_by_task`` / ``list_attached_tasks`` fully *loaded* every
  session just to scan its task ids — the worst offender as sessions pile up.

Those become single indexed SQL queries. The index is maintained
incrementally on each store mutation and can be rebuilt from disk at any time
(``rebuild``), so it is always disposable: delete ``index.db`` and it heals.

Reliability stance (mechanism, DEEPCODE_V2_MASTER_PLAN.md §3.4): the index is
an *optimisation*, never a correctness dependency. Every store method that
uses it falls back to the JSONL scan if SQLite is unavailable or errors, and
reconciles itself when it notices the on-disk session count has drifted (e.g.
sessions written by an older build that had no index).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.sessions.models import SessionSummary, SessionTask

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 0,
    task_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
CREATE TABLE IF NOT EXISTS session_tasks (
    task_id    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_kind  TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON session_tasks(session_id);
"""


class SessionIndex:
    """A thread-safe SQLite index of session summaries and task→session links.

    Construction never raises on a bad path/permission — it degrades to a
    disabled index (:attr:`available` is ``False``) so the store keeps working
    off JSONL alone.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False + our own RLock: the store touches this
            # from whatever thread services a request; we serialise ourselves.
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        except (sqlite3.Error, OSError):
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    # -- mutations ---------------------------------------------------------

    def upsert_session(self, summary: SessionSummary) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, title, created_at, updated_at, "
                    " message_count, task_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        summary.session_id,
                        summary.title,
                        summary.created_at,
                        summary.updated_at,
                        summary.message_count,
                        summary.task_count,
                    ),
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    def upsert_task(self, session_id: str, task: SessionTask) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO session_tasks "
                    "(task_id, session_id, task_kind, status) VALUES (?, ?, ?, ?)",
                    (task.task_id, session_id, task.task_kind, task.status),
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "DELETE FROM session_tasks WHERE session_id = ?", (session_id,)
                )
                self._conn.execute(
                    "DELETE FROM sessions WHERE session_id = ?", (session_id,)
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    def rebuild(
        self,
        summaries: list[SessionSummary],
        task_links: list[tuple[str, SessionTask]],
    ) -> None:
        """Replace the whole index from a full disk scan (the cold path)."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("DELETE FROM session_tasks")
                self._conn.execute("DELETE FROM sessions")
                self._conn.executemany(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, title, created_at, updated_at, "
                    " message_count, task_count) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            s.session_id,
                            s.title,
                            s.created_at,
                            s.updated_at,
                            s.message_count,
                            s.task_count,
                        )
                        for s in summaries
                    ],
                )
                self._conn.executemany(
                    "INSERT OR REPLACE INTO session_tasks "
                    "(task_id, session_id, task_kind, status) VALUES (?, ?, ?, ?)",
                    [(t.task_id, sid, t.task_kind, t.status) for sid, t in task_links],
                )
                self._conn.commit()
            except sqlite3.Error:
                pass

    # -- queries -----------------------------------------------------------

    def session_count(self) -> int:
        with self._lock:
            if self._conn is None:
                return 0
            try:
                row = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error:
                return 0

    def list_summaries(self, *, limit: int, order: str) -> list[SessionSummary] | None:
        """Return ordered summaries, or ``None`` if the index is unusable."""
        with self._lock:
            if self._conn is None:
                return None
            direction = "ASC" if order == "oldest" else "DESC"
            try:
                rows = self._conn.execute(
                    "SELECT session_id, title, created_at, updated_at, "
                    "message_count, task_count FROM sessions "
                    f"ORDER BY updated_at {direction} LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
            except sqlite3.Error:
                return None
            from core.sessions.models import SessionSummary

            return [
                SessionSummary(
                    session_id=r[0],
                    title=r[1],
                    created_at=r[2],
                    updated_at=r[3],
                    message_count=r[4],
                    task_count=r[5],
                )
                for r in rows
            ]

    def session_id_for_task(self, task_id: str) -> str | None:
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute(
                    "SELECT session_id FROM session_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                return row[0] if row else None
            except sqlite3.Error:
                return None

    def all_task_links(self) -> list[tuple[str, str]] | None:
        """Every ``(session_id, task_id)`` pair, or ``None`` if unusable."""
        with self._lock:
            if self._conn is None:
                return None
            try:
                rows = self._conn.execute(
                    "SELECT session_id, task_id FROM session_tasks"
                ).fetchall()
                return [(r[0], r[1]) for r in rows]
            except sqlite3.Error:
                return None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
