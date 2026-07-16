"""Append-only thread event log."""

from __future__ import annotations

import sqlite3

from core.domain.common import JsonObject
from core.domain.event import DomainEvent
from core.persistence.serde import (
    dump_datetime,
    dump_json,
    load_json,
    load_required_datetime,
)


class EventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(
        self,
        *,
        thread_id: str,
        type: str,
        payload: JsonObject,
        turn_id: str | None = None,
        item_id: str | None = None,
    ) -> DomainEvent:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM event_log WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        event = DomainEvent(
            sequence=int(row[0]),
            type=type,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            payload=payload,
        )
        self.connection.execute(
            "INSERT INTO event_log (id, thread_id, sequence, type, turn_id, item_id, "
            "timestamp, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.thread_id,
                event.sequence,
                event.type,
                event.turn_id,
                event.item_id,
                dump_datetime(event.timestamp),
                dump_json(event.payload),
            ),
        )
        return event

    def replay(
        self,
        thread_id: str,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> list[DomainEvent]:
        rows = self.connection.execute(
            "SELECT * FROM event_log WHERE thread_id = ? AND sequence > ? "
            "ORDER BY sequence LIMIT ?",
            (thread_id, after, limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def has_type(self, thread_id: str, event_type: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM event_log WHERE thread_id = ? AND type = ? LIMIT 1",
            (thread_id, event_type),
        ).fetchone()
        return row is not None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> DomainEvent:
        return DomainEvent(
            id=row["id"],
            thread_id=row["thread_id"],
            sequence=row["sequence"],
            type=row["type"],
            turn_id=row["turn_id"],
            item_id=row["item_id"],
            timestamp=load_required_datetime(row["timestamp"]),
            payload=load_json(row["payload_json"]),
        )
