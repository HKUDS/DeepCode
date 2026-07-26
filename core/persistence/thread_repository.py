"""Thread persistence mapping."""

from __future__ import annotations

import sqlite3

from core.domain.thread import Thread, ThreadMode, ThreadStatus
from core.persistence.serde import (
    dump_datetime,
    load_datetime,
    load_required_datetime,
)


class ThreadRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, thread: Thread) -> None:
        self.connection.execute(
            "INSERT INTO threads (id, project_id, parent_thread_id, title, mode, "
            "status, model, connection_id, workspace_path, worktree_path, "
            "reasoning_effort, created_at, updated_at, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread.id,
                thread.project_id,
                thread.parent_thread_id,
                thread.title,
                thread.mode.value,
                thread.status.value,
                thread.model,
                thread.connection_id,
                thread.workspace_path,
                thread.worktree_path,
                thread.reasoning_effort,
                dump_datetime(thread.created_at),
                dump_datetime(thread.updated_at),
                dump_datetime(thread.archived_at),
            ),
        )

    def update(self, thread: Thread) -> None:
        cursor = self.connection.execute(
            "UPDATE threads SET project_id = ?, parent_thread_id = ?, title = ?, "
            "mode = ?, status = ?, model = ?, connection_id = ?, "
            "workspace_path = ?, worktree_path = ?, reasoning_effort = ?, "
            "updated_at = ?, archived_at = ? WHERE id = ?",
            (
                thread.project_id,
                thread.parent_thread_id,
                thread.title,
                thread.mode.value,
                thread.status.value,
                thread.model,
                thread.connection_id,
                thread.workspace_path,
                thread.worktree_path,
                thread.reasoning_effort,
                dump_datetime(thread.updated_at),
                dump_datetime(thread.archived_at),
                thread.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(thread.id)

    def get(self, thread_id: str) -> Thread | None:
        row = self.connection.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_for_project(
        self,
        project_id: str,
        *,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Thread]:
        archived_clause = "" if include_archived else "AND status <> 'archived'"
        rows = self.connection.execute(
            "SELECT * FROM threads WHERE project_id = ? "
            f"{archived_clause} ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
            (project_id, limit, offset),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_all(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Thread]:
        archived_clause = "" if include_archived else "WHERE status <> 'archived'"
        rows = self.connection.execute(
            f"SELECT * FROM threads {archived_clause} "
            "ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def remove(self, thread_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM threads WHERE id = ?", (thread_id,)
        )
        return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Thread:
        return Thread(
            id=row["id"],
            project_id=row["project_id"],
            parent_thread_id=row["parent_thread_id"],
            title=row["title"],
            mode=ThreadMode(row["mode"]),
            status=ThreadStatus(row["status"]),
            model=row["model"],
            connection_id=row["connection_id"],
            reasoning_effort=row["reasoning_effort"],
            workspace_path=row["workspace_path"],
            worktree_path=row["worktree_path"],
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
            archived_at=load_datetime(row["archived_at"]),
        )
