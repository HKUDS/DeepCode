"""Idempotency records for read-only legacy imports."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from core.persistence.serde import dump_datetime


class LegacyImportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def imported_thread_id(self, source_key: str) -> str | None:
        row = self.connection.execute(
            "SELECT thread_id FROM legacy_imports WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        return str(row["thread_id"]) if row is not None else None

    def source_for_thread(self, thread_id: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT source_key, source_session_id FROM legacy_imports "
            "WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["source_key"]), str(row["source_session_id"])

    def add(
        self,
        *,
        source_key: str,
        source_session_id: str,
        project_id: str,
        thread_id: str,
        imported_at: datetime,
    ) -> None:
        self.connection.execute(
            "INSERT INTO legacy_imports "
            "(source_key, source_session_id, project_id, thread_id, imported_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                source_key,
                source_session_id,
                project_id,
                thread_id,
                dump_datetime(imported_at),
            ),
        )
