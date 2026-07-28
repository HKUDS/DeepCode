"""Persistence mappings for turns, items, and approvals."""

from __future__ import annotations

import sqlite3

from core.domain.approval import Approval, ApprovalCategory, ApprovalStatus
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.turn import Turn, TurnStatus
from core.domain.execution_profile import ExecutionProfile
from core.persistence.serde import (
    dump_datetime,
    dump_json,
    load_datetime,
    load_json,
    load_json_list,
    load_required_datetime,
)


class TurnRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def next_ordinal(self, thread_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM turns WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return int(row[0])

    def add(self, turn: Turn) -> None:
        self.connection.execute(
            "INSERT INTO turns (id, thread_id, ordinal, prompt, skill_ids_json, "
            "execution_profile_json, goal_id, status, stop_reason, "
            "error_code, error_message, "
            "started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                turn.id,
                turn.thread_id,
                turn.ordinal,
                turn.prompt,
                dump_json(list(turn.skill_ids)),
                (
                    dump_json(turn.execution_profile.to_dict())
                    if turn.execution_profile is not None
                    else None
                ),
                turn.goal_id,
                turn.status.value,
                turn.stop_reason,
                turn.error_code,
                turn.error_message,
                dump_datetime(turn.started_at),
                dump_datetime(turn.completed_at),
            ),
        )

    def get(self, turn_id: str) -> Turn | None:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def update(self, turn: Turn) -> None:
        cursor = self.connection.execute(
            "UPDATE turns SET execution_profile_json = ?, status = ?, "
            "stop_reason = ?, error_code = ?, "
            "error_message = ?, started_at = ?, completed_at = ? WHERE id = ?",
            (
                (
                    dump_json(turn.execution_profile.to_dict())
                    if turn.execution_profile is not None
                    else None
                ),
                turn.status.value,
                turn.stop_reason,
                turn.error_code,
                turn.error_message,
                dump_datetime(turn.started_at),
                dump_datetime(turn.completed_at),
                turn.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(turn.id)

    def active_for_thread(self, thread_id: str) -> Turn | None:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE thread_id = ? AND status IN "
            "('queued', 'running', 'waiting_approval') "
            "ORDER BY CASE status "
            "WHEN 'running' THEN 0 WHEN 'waiting_approval' THEN 0 ELSE 1 END, "
            "ordinal LIMIT 1",
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def executing_for_thread(self, thread_id: str) -> Turn | None:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE thread_id = ? "
            "AND status IN ('running', 'waiting_approval') "
            "ORDER BY ordinal LIMIT 1",
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def next_queued_for_thread(self, thread_id: str) -> Turn | None:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE thread_id = ? AND status = 'queued' "
            "ORDER BY ordinal LIMIT 1",
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_active(self) -> list[Turn]:
        rows = self.connection.execute(
            "SELECT * FROM turns WHERE status IN "
            "('queued', 'running', 'waiting_approval') ORDER BY thread_id, ordinal"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_thread(self, thread_id: str) -> list[Turn]:
        rows = self.connection.execute(
            "SELECT * FROM turns WHERE thread_id = ? ORDER BY ordinal", (thread_id,)
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Turn:
        return Turn(
            id=row["id"],
            thread_id=row["thread_id"],
            ordinal=row["ordinal"],
            prompt=row["prompt"],
            skill_ids=tuple(load_json_list(row["skill_ids_json"])),
            execution_profile=ExecutionProfile.from_dict(
                load_json(row["execution_profile_json"])
                if row["execution_profile_json"] is not None
                else None
            ),
            goal_id=row["goal_id"],
            status=TurnStatus(row["status"]),
            stop_reason=row["stop_reason"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=load_datetime(row["started_at"]),
            completed_at=load_datetime(row["completed_at"]),
        )


class ItemRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def next_ordinal(self, turn_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM items WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        return int(row[0])

    def add(self, item: Item) -> None:
        self.connection.execute(
            "INSERT INTO items (id, thread_id, turn_id, ordinal, kind, status, "
            "summary, payload_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.thread_id,
                item.turn_id,
                item.ordinal,
                item.kind.value,
                item.status.value,
                item.summary,
                dump_json(item.payload),
                dump_datetime(item.created_at),
                dump_datetime(item.updated_at),
            ),
        )

    def get(self, item_id: str) -> Item | None:
        row = self.connection.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def update(self, item: Item) -> None:
        cursor = self.connection.execute(
            "UPDATE items SET status = ?, summary = ?, payload_json = ?, "
            "updated_at = ? WHERE id = ?",
            (
                item.status.value,
                item.summary,
                dump_json(item.payload),
                dump_datetime(item.updated_at),
                item.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(item.id)

    def list_active_for_turn(self, turn_id: str) -> list[Item]:
        rows = self.connection.execute(
            "SELECT * FROM items WHERE turn_id = ? AND status IN "
            "('pending', 'in_progress') ORDER BY ordinal",
            (turn_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def conversation_count(self, thread_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM items WHERE thread_id = ? "
            "AND kind IN ('user_message', 'assistant_message')",
            (thread_id,),
        ).fetchone()
        return int(row[0])

    def find_user_message_by_message_id(
        self,
        thread_id: str,
        message_id: str,
    ) -> Item | None:
        row = self.connection.execute(
            "SELECT * FROM items WHERE thread_id = ? "
            "AND kind = 'user_message' "
            "AND json_extract(payload_json, '$.messageId') = ? "
            "ORDER BY created_at, id LIMIT 1",
            (thread_id, message_id),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def conversation_before(self, thread_id: str, turn_ordinal: int) -> list[Item]:
        rows = self.connection.execute(
            "SELECT items.* FROM items JOIN turns ON turns.id = items.turn_id "
            "WHERE items.thread_id = ? AND turns.ordinal < ? "
            "AND items.kind IN ('user_message', 'assistant_message') "
            "AND items.status = 'completed' "
            "ORDER BY turns.ordinal, items.ordinal",
            (thread_id, turn_ordinal),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def conversation_for_thread(self, thread_id: str) -> list[Item]:
        rows = self.connection.execute(
            "SELECT items.* FROM items JOIN turns ON turns.id = items.turn_id "
            "WHERE items.thread_id = ? "
            "AND items.kind IN ('user_message', 'assistant_message') "
            "AND items.status = 'completed' "
            "ORDER BY turns.ordinal, items.ordinal",
            (thread_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_turn(self, turn_id: str) -> list[Item]:
        rows = self.connection.execute(
            "SELECT * FROM items WHERE turn_id = ? ORDER BY ordinal", (turn_id,)
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Item:
        return Item(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            ordinal=row["ordinal"],
            kind=ItemKind(row["kind"]),
            status=ItemStatus(row["status"]),
            summary=row["summary"],
            payload=load_json(row["payload_json"]),
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
        )


class ApprovalRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, approval: Approval) -> None:
        self.connection.execute(
            "INSERT INTO approvals (id, thread_id, turn_id, item_id, category, status, "
            "request_json, decision_json, requested_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval.id,
                approval.thread_id,
                approval.turn_id,
                approval.item_id,
                approval.category.value,
                approval.status.value,
                dump_json(approval.request),
                dump_json(approval.decision) if approval.decision is not None else None,
                dump_datetime(approval.requested_at),
                dump_datetime(approval.resolved_at),
            ),
        )

    def get(self, approval_id: str) -> Approval | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def update(self, approval: Approval) -> None:
        cursor = self.connection.execute(
            "UPDATE approvals SET status = ?, decision_json = ?, resolved_at = ? "
            "WHERE id = ?",
            (
                approval.status.value,
                dump_json(approval.decision) if approval.decision is not None else None,
                dump_datetime(approval.resolved_at),
                approval.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(approval.id)

    def list_for_turn(self, turn_id: str) -> list[Approval]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE turn_id = ? ORDER BY requested_at, id",
            (turn_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def pending_for_turn(self, turn_id: str) -> list[Approval]:
        rows = self.connection.execute(
            "SELECT * FROM approvals WHERE turn_id = ? AND status = 'pending' "
            "ORDER BY requested_at, id",
            (turn_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            item_id=row["item_id"],
            category=ApprovalCategory(row["category"]),
            status=ApprovalStatus(row["status"]),
            request=load_json(row["request_json"]),
            decision=load_json(row["decision_json"])
            if row["decision_json"] is not None
            else None,
            requested_at=load_required_datetime(row["requested_at"]),
            resolved_at=load_datetime(row["resolved_at"]),
        )
