"""Persistence mappings for Desktop automation definitions and execution facts."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from core.domain.automation import (
    Automation,
    AutomationOccurrence,
    AutomationRevision,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
)
from core.domain.common import utc_now
from core.persistence.serde import (
    dump_datetime,
    load_datetime,
    load_required_datetime,
)

_AUTOMATION_SELECT = """
SELECT
    automation.id,
    automation.project_id,
    automation.thread_id,
    automation.name,
    automation.current_revision_id,
    revision.instruction AS prompt,
    automation.status,
    automation.schedule_kind,
    automation.interval_seconds,
    automation.next_run_at,
    automation.last_run_at,
    automation.created_at,
    automation.updated_at
FROM automations AS automation
JOIN automation_revisions AS revision
    ON revision.id = automation.current_revision_id
    AND revision.automation_id = automation.id
"""

_OPEN_RUN_STATUSES = (
    AutomationRunStatus.QUEUED,
    AutomationRunStatus.RUNNING,
    AutomationRunStatus.WAITING,
    AutomationRunStatus.BLOCKED,
)


class AutomationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, automation: Automation) -> None:
        """Add a definition after its initial revision has been staged.

        ``AutomationRevisionRepository.add`` may run first in the same write
        transaction because the schema's parent foreign key is deferred.
        """

        self._require_matching_revision(automation)
        self.connection.execute(
            "INSERT INTO automations ("
            "id, project_id, thread_id, name, current_revision_id, status, "
            "schedule_kind, interval_seconds, next_run_at, last_run_at, "
            "created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                automation.id,
                automation.project_id,
                automation.thread_id,
                automation.name,
                automation.current_revision_id,
                automation.status.value,
                automation.schedule_kind.value,
                automation.interval_seconds,
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.created_at),
                dump_datetime(automation.updated_at),
            ),
        )

    def update(
        self,
        automation: Automation,
        *,
        expected_current_revision_id: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        """Persist a definition, optionally guarded by its previously read state.

        A guarded stale write returns ``False``. A missing or retired definition
        continues to raise ``KeyError``; unguarded callers therefore retain the
        previous success-or-error contract.
        """

        self._require_matching_revision(automation)
        predicates = ["id = ?", "status <> 'retired'"]
        predicate_values: list[object] = [automation.id]
        if expected_current_revision_id is not None:
            predicates.append("current_revision_id = ?")
            predicate_values.append(expected_current_revision_id)
        if expected_updated_at is not None:
            predicates.append("updated_at = ?")
            predicate_values.append(dump_datetime(expected_updated_at))
        cursor = self.connection.execute(
            "UPDATE automations SET name = ?, current_revision_id = ?, status = ?, "
            "schedule_kind = ?, interval_seconds = ?, next_run_at = ?, "
            "last_run_at = ?, updated_at = ? WHERE " + " AND ".join(predicates),
            (
                automation.name,
                automation.current_revision_id,
                automation.status.value,
                automation.schedule_kind.value,
                automation.interval_seconds,
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.updated_at),
                *predicate_values,
            ),
        )
        if cursor.rowcount == 1:
            return True
        row = self.connection.execute(
            "SELECT status FROM automations WHERE id = ?",
            (automation.id,),
        ).fetchone()
        if row is None or row["status"] == AutomationStatus.RETIRED.value:
            raise KeyError(automation.id)
        if expected_current_revision_id is not None or expected_updated_at is not None:
            return False
        raise KeyError(automation.id)

    def claim_due(
        self,
        automation: Automation,
        *,
        expected_next_run_at: datetime,
    ) -> bool:
        """Advance one exact due occurrence once across concurrent processes."""

        cursor = self.connection.execute(
            "UPDATE automations SET next_run_at = ?, last_run_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'enabled' "
            "AND schedule_kind = 'interval' AND next_run_at = ?",
            (
                dump_datetime(automation.next_run_at),
                dump_datetime(automation.last_run_at),
                dump_datetime(automation.updated_at),
                automation.id,
                dump_datetime(expected_next_run_at),
            ),
        )
        return cursor.rowcount == 1

    def get(
        self,
        automation_id: str,
        *,
        include_retired: bool = False,
    ) -> Automation | None:
        retired_filter = (
            "" if include_retired else " AND automation.status <> 'retired'"
        )
        row = self.connection.execute(
            _AUTOMATION_SELECT + "WHERE automation.id = ?" + retired_filter,
            (automation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_for_thread(
        self,
        thread_id: str,
        *,
        include_retired: bool = False,
    ) -> Automation | None:
        retired_filter = (
            "" if include_retired else " AND automation.status <> 'retired'"
        )
        row = self.connection.execute(
            _AUTOMATION_SELECT + "WHERE automation.thread_id = ?" + retired_filter,
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        project_id: str | None = None,
        include_retired: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Automation]:
        predicates: list[str] = []
        values: list[object] = []
        if project_id is not None:
            predicates.append("automation.project_id = ?")
            values.append(project_id)
        if not include_retired:
            predicates.append("automation.status <> 'retired'")
        where = f"WHERE {' AND '.join(predicates)} " if predicates else ""
        rows = self.connection.execute(
            _AUTOMATION_SELECT
            + where
            + "ORDER BY automation.updated_at DESC, automation.id DESC "
            "LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_due(self, now: datetime, *, limit: int = 100) -> list[Automation]:
        rows = self.connection.execute(
            _AUTOMATION_SELECT + "WHERE automation.status = 'enabled' "
            "AND automation.schedule_kind = 'interval' "
            "AND automation.next_run_at IS NOT NULL "
            "AND automation.next_run_at <= ? "
            "ORDER BY automation.next_run_at, automation.id LIMIT ?",
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
        """Soft-retire a definition while preserving its immutable history."""

        cursor = self.connection.execute(
            "UPDATE automations SET status = 'retired', next_run_at = NULL, "
            "updated_at = ? WHERE id = ? AND status <> 'retired'",
            (dump_datetime(utc_now()), automation_id),
        )
        return cursor.rowcount == 1

    def _require_matching_revision(self, automation: Automation) -> None:
        row = self.connection.execute(
            "SELECT automation_id, instruction FROM automation_revisions WHERE id = ?",
            (automation.current_revision_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                "automation current revision must be staged before the definition"
            )
        if row["automation_id"] != automation.id:
            raise ValueError(
                "automation current revision belongs to another definition"
            )
        if row["instruction"] != automation.prompt:
            raise ValueError(
                "automation prompt projection must match its current revision"
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Automation:
        return Automation(
            id=row["id"],
            project_id=row["project_id"],
            thread_id=row["thread_id"],
            name=row["name"],
            current_revision_id=row["current_revision_id"],
            prompt=row["prompt"],
            status=AutomationStatus(row["status"]),
            schedule_kind=AutomationScheduleKind(row["schedule_kind"]),
            interval_seconds=row["interval_seconds"],
            next_run_at=load_datetime(row["next_run_at"]),
            last_run_at=load_datetime(row["last_run_at"]),
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
        )


class AutomationRevisionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, revision: AutomationRevision) -> None:
        self.connection.execute(
            "INSERT INTO automation_revisions ("
            "id, automation_id, ordinal, instruction, created_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                revision.id,
                revision.automation_id,
                revision.ordinal,
                revision.instruction,
                dump_datetime(revision.created_at),
            ),
        )

    def get(self, revision_id: str) -> AutomationRevision | None:
        row = self.connection.execute(
            "SELECT * FROM automation_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_current(self, automation_id: str) -> AutomationRevision | None:
        row = self.connection.execute(
            "SELECT revision.* FROM automation_revisions AS revision "
            "JOIN automations AS automation "
            "ON automation.current_revision_id = revision.id "
            "AND automation.id = revision.automation_id "
            "WHERE automation.id = ?",
            (automation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def next_ordinal(self, automation_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 "
            "FROM automation_revisions WHERE automation_id = ?",
            (automation_id,),
        ).fetchone()
        return int(row[0])

    def list_for_automation(
        self,
        automation_id: str,
    ) -> list[AutomationRevision]:
        rows = self.connection.execute(
            "SELECT * FROM automation_revisions WHERE automation_id = ? "
            "ORDER BY ordinal",
            (automation_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AutomationRevision:
        return AutomationRevision(
            id=row["id"],
            automation_id=row["automation_id"],
            ordinal=row["ordinal"],
            instruction=row["instruction"],
            created_at=load_required_datetime(row["created_at"]),
        )


class AutomationOccurrenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, occurrence: AutomationOccurrence) -> None:
        self.connection.execute(
            "INSERT INTO automation_occurrences ("
            "id, automation_id, kind, occurrence_key, nominal_at, observed_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                occurrence.id,
                occurrence.automation_id,
                occurrence.kind.value,
                occurrence.occurrence_key,
                dump_datetime(occurrence.nominal_at),
                dump_datetime(occurrence.observed_at),
            ),
        )

    def get(self, occurrence_id: str) -> AutomationOccurrence | None:
        row = self.connection.execute(
            "SELECT * FROM automation_occurrences WHERE id = ?",
            (occurrence_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_by_key(
        self,
        automation_id: str,
        kind: AutomationTrigger,
        occurrence_key: str,
    ) -> AutomationOccurrence | None:
        row = self.connection.execute(
            "SELECT * FROM automation_occurrences "
            "WHERE automation_id = ? AND kind = ? AND occurrence_key = ?",
            (automation_id, kind.value, occurrence_key),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AutomationOccurrence:
        return AutomationOccurrence(
            id=row["id"],
            automation_id=row["automation_id"],
            kind=AutomationTrigger(row["kind"]),
            occurrence_key=row["occurrence_key"],
            nominal_at=load_required_datetime(row["nominal_at"]),
            observed_at=load_required_datetime(row["observed_at"]),
        )


class AutomationRunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, run: AutomationRun) -> None:
        self.connection.execute(
            "INSERT INTO automation_runs ("
            "id, automation_id, revision_id, occurrence_id, goal_id, thread_id, "
            "turn_id, trigger, status, scheduled_for, detail, created_at, "
            "updated_at, started_at, completed_at, version"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.automation_id,
                run.revision_id,
                run.occurrence_id,
                run.goal_id,
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
                run.version,
            ),
        )

    def update(
        self,
        run: AutomationRun,
        *,
        expected_status: AutomationRunStatus | None = None,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        """Persist mutable Run state with versioned compare-and-set.

        ``run.version`` is always the expected current version; SQLite advances
        it exactly once. A stale write returns ``False`` and a missing Run
        raises ``KeyError``.
        """

        predicates = ["id = ?", "version = ?"]
        predicate_values: list[object] = [run.id, run.version]
        if expected_status is not None:
            predicates.append("status = ?")
            predicate_values.append(expected_status.value)
        if expected_updated_at is not None:
            predicates.append("updated_at = ?")
            predicate_values.append(dump_datetime(expected_updated_at))
        cursor = self.connection.execute(
            "UPDATE automation_runs SET goal_id = ?, turn_id = ?, status = ?, "
            "detail = ?, updated_at = ?, started_at = ?, completed_at = ?, "
            "version = version + 1 "
            "WHERE " + " AND ".join(predicates),
            (
                run.goal_id,
                run.turn_id,
                run.status.value,
                run.detail,
                dump_datetime(run.updated_at),
                dump_datetime(run.started_at),
                dump_datetime(run.completed_at),
                *predicate_values,
            ),
        )
        if cursor.rowcount == 1:
            return True
        row = self.connection.execute(
            "SELECT 1 FROM automation_runs WHERE id = ?",
            (run.id,),
        ).fetchone()
        if row is None:
            raise KeyError(run.id)
        return False

    def get(self, run_id: str) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_for_occurrence(
        self,
        occurrence_id: str,
    ) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_for_goal(self, goal_id: str) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def pending_initial_for_thread(
        self,
        thread_id: str,
    ) -> AutomationRun | None:
        """Return the open Run that still owns initial-Turn admission.

        Automation Threads are definition-owned today, but the query is
        deliberately expressed in terms of the durable Run and Thread so Turn
        admission remains correct if the product later supports shared Threads.
        """

        placeholders = ", ".join("?" for _ in _OPEN_RUN_STATUSES)
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE thread_id = ? "
            "AND turn_id IS NULL "
            f"AND status IN ({placeholders}) "
            "ORDER BY created_at, id LIMIT 1",
            (thread_id, *(status.value for status in _OPEN_RUN_STATUSES)),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def open_for_automation(
        self,
        automation_id: str,
    ) -> AutomationRun | None:
        placeholders = ", ".join("?" for _ in _OPEN_RUN_STATUSES)
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? "
            f"AND status IN ({placeholders}) ORDER BY created_at DESC, id LIMIT 1",
            (automation_id, *(status.value for status in _OPEN_RUN_STATUSES)),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def open_for_thread(self, thread_id: str) -> AutomationRun | None:
        placeholders = ", ".join("?" for _ in _OPEN_RUN_STATUSES)
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE thread_id = ? "
            f"AND status IN ({placeholders}) "
            "ORDER BY created_at DESC, id LIMIT 1",
            (thread_id, *(status.value for status in _OPEN_RUN_STATUSES)),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_for_automation(
        self,
        automation_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AutomationRun]:
        rows = self.connection.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? "
            "ORDER BY scheduled_for DESC, id DESC LIMIT ? OFFSET ?",
            (automation_id, limit, offset),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def latest_for_automation(
        self,
        automation_id: str,
    ) -> AutomationRun | None:
        row = self.connection.execute(
            "SELECT * FROM automation_runs WHERE automation_id = ? "
            "ORDER BY scheduled_for DESC, id DESC LIMIT 1",
            (automation_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_active(self) -> list[AutomationRun]:
        placeholders = ", ".join("?" for _ in _OPEN_RUN_STATUSES)
        rows = self.connection.execute(
            "SELECT * FROM automation_runs "
            f"WHERE status IN ({placeholders}) ORDER BY created_at",
            tuple(status.value for status in _OPEN_RUN_STATUSES),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AutomationRun:
        return AutomationRun(
            id=row["id"],
            automation_id=row["automation_id"],
            revision_id=row["revision_id"],
            occurrence_id=row["occurrence_id"],
            goal_id=row["goal_id"],
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
            version=int(row["version"]),
        )
