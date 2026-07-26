"""Persistence mappings for Desktop automations and their runs."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from core.domain.automation import (
    Automation,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.persistence.serde import (
    dump_datetime,
    load_datetime,
    load_required_datetime,
)


class AutomationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, automation: Automation) -> None:
        self.connection.execute(
            "INSERT INTO automations ("
            "id, project_id, thread_id, name, prompt, status, schedule_kind, "
            "interval_seconds, next_run_at, last_run_at, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                automation.id,
                automation.project_id,
                automation.thread_id,
                automation.name,
                automation.prompt,
                automation.status.value,
                automation.schedule_kind.value,
                automation.interval_seconds,
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.created_at),
                dump_datetime(automation.updated_at),
            ),
        )

    def update(self, automation: Automation) -> None:
        cursor = self.connection.execute(
            "UPDATE automations SET name = ?, prompt = ?, status = ?, "
            "schedule_kind = ?, interval_seconds = ?, next_run_at = ?, "
            "last_run_at = ?, updated_at = ? WHERE id = ?",
            (
                automation.name,
                automation.prompt,
                automation.status.value,
                automation.schedule_kind.value,
                automation.interval_seconds,
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.updated_at),
                automation.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(automation.id)

    def claim_due(
        self,
        automation: Automation,
        *,
        expected_next_run_at: datetime,
    ) -> bool:
        """Advance one exact due occurrence once across concurrent processes."""

        cursor = self.connection.execute(
            "UPDATE automations SET name = ?, prompt = ?, status = ?, "
            "schedule_kind = ?, interval_seconds = ?, next_run_at = ?, "
            "last_run_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'enabled' "
            "AND schedule_kind = 'interval' AND next_run_at = ?",
            (
                automation.name,
                automation.prompt,
                automation.status.value,
                automation.schedule_kind.value,
                automation.interval_seconds,
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.updated_at),
                automation.id,
                dump_datetime(expected_next_run_at),
            ),
        )
        return cursor.rowcount == 1

    def get(self, automation_id: str) -> Automation | None:
        row = self.connection.execute(
            "SELECT * FROM automations WHERE id = ?",
            (automation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_for_thread(self, thread_id: str) -> Automation | None:
        row = self.connection.execute(
            "SELECT * FROM automations WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        project_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Automation]:
        if project_id is None:
            rows = self.connection.execute(
                "SELECT * FROM automations "
                "ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM automations WHERE project_id = ? "
                "ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_due(self, now: datetime, *, limit: int = 100) -> list[Automation]:
        rows = self.connection.execute(
            "SELECT * FROM automations "
            "WHERE status = 'enabled' AND schedule_kind = 'interval' "
            "AND next_run_at IS NOT NULL AND next_run_at <= ? "
            "ORDER BY next_run_at, id LIMIT ?",
            (dump_datetime(now), limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def next_due_at(self) -> datetime | None:
        row = self.connection.execute(
            "SELECT MIN(next_run_at) FROM automations "
            "WHERE status = 'enabled' AND schedule_kind = 'interval' "
            "AND next_run_at IS NOT NULL"
        ).fetchone()
        return load_datetime(row[0]) if row and row[0] is not None else None

    def remove(self, automation_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM automations WHERE id = ?",
            (automation_id,),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Automation:
        return Automation(
            id=row["id"],
            project_id=row["project_id"],
            thread_id=row["thread_id"],
            name=row["name"],
            prompt=row["prompt"],
            status=AutomationStatus(row["status"]),
            schedule_kind=AutomationScheduleKind(row["schedule_kind"]),
            interval_seconds=row["interval_seconds"],
            next_run_at=load_datetime(row["next_run_at"]),
            last_run_at=load_datetime(row["last_run_at"]),
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
        )


class AutomationRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, run: AutomationRun) -> None:
        self.connection.execute(
            "INSERT INTO automation_runs ("
            "id, automation_id, thread_id, turn_id, trigger, status, "
            "scheduled_for, detail, created_at, updated_at, started_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.automation_id,
                run.thread_id,
                run.turn_id,
                run.trigger.value,
                run.status.value,
                dump_datetime(run.scheduled_for),
                run.detail,
                dump_datetime(run.created_at),
                dump_datetime(run.updated_at),
                dump_datetime(run.started_at),
                dump_datetime(run.completed_at),
            ),
        )

    def update(self, run: AutomationRun) -> None:
        cursor = self.connection.execute(
            "UPDATE automation_runs SET turn_id = ?, status = ?, detail = ?, "
            "updated_at = ?, started_at = ?, completed_at = ? WHERE id = ?",
            (
                run.turn_id,
                run.status.value,
                run.detail,
                dump_datetime(run.updated_at),
                dump_datetime(run.started_at),
                dump_datetime(run.completed_at),
                run.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(run.id)

    def get(self, run_id: str) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_for_automation(
        self,
        automation_id: str,
        *,
        limit: int = 100,
    ) -> list[AutomationRun]:
        rows = self.connection.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? "
            "ORDER BY created_at DESC, id LIMIT ?",
            (automation_id, limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def latest_for_automation(
        self,
        automation_id: str,
    ) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? "
            "ORDER BY created_at DESC, id LIMIT 1",
            (automation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_active(self) -> list[AutomationRun]:
        rows = self.connection.execute(
            "SELECT * FROM automation_runs "
            "WHERE status IN ('queued', 'running', 'waiting') "
            "ORDER BY created_at"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AutomationRun:
        return AutomationRun(
            id=row["id"],
            automation_id=row["automation_id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            trigger=AutomationTrigger(row["trigger"]),
            status=AutomationRunStatus(row["status"]),
            scheduled_for=load_required_datetime(row["scheduled_for"]),
            detail=row["detail"],
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
            started_at=load_datetime(row["started_at"]),
            completed_at=load_datetime(row["completed_at"]),
        )
