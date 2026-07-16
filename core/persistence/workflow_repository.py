"""Persistence mappings for workflow runs and artifacts."""

from __future__ import annotations

import sqlite3

from core.domain.artifact import Artifact
from core.domain.workflow import WorkflowRun, WorkflowStatus
from core.persistence.serde import (
    dump_datetime,
    dump_json,
    load_datetime,
    load_json,
    load_required_datetime,
)


class WorkflowRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, run: WorkflowRun) -> None:
        self.connection.execute(
            "INSERT INTO workflow_runs (id, thread_id, turn_id, kind, status, "
            "input_json, result_json, attempt, retry_of, current_stage, progress_current, "
            "progress_total, checkpoint_json, created_at, updated_at, started_at, "
            "completed_at, error_code, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.thread_id,
                run.turn_id,
                run.kind,
                run.status.value,
                dump_json(run.input),
                dump_json(run.result),
                run.attempt,
                run.retry_of,
                run.current_stage,
                run.progress_current,
                run.progress_total,
                dump_json(run.checkpoint),
                dump_datetime(run.created_at),
                dump_datetime(run.updated_at),
                dump_datetime(run.started_at),
                dump_datetime(run.completed_at),
                run.error_code,
                run.error_message,
            ),
        )

    def update(self, run: WorkflowRun) -> None:
        cursor = self.connection.execute(
            "UPDATE workflow_runs SET status = ?, input_json = ?, result_json = ?, "
            "attempt = ?, retry_of = ?, current_stage = ?, progress_current = ?, "
            "progress_total = ?, checkpoint_json = ?, updated_at = ?, started_at = ?, "
            "completed_at = ?, error_code = ?, error_message = ? WHERE id = ?",
            (
                run.status.value,
                dump_json(run.input),
                dump_json(run.result),
                run.attempt,
                run.retry_of,
                run.current_stage,
                run.progress_current,
                run.progress_total,
                dump_json(run.checkpoint),
                dump_datetime(run.updated_at),
                dump_datetime(run.started_at),
                dump_datetime(run.completed_at),
                run.error_code,
                run.error_message,
                run.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(run.id)

    def get(self, run_id: str) -> WorkflowRun | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list_for_thread(self, thread_id: str, *, limit: int = 100) -> list[WorkflowRun]:
        rows = self.connection.execute(
            "SELECT * FROM workflow_runs WHERE thread_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def active_for_thread(self, thread_id: str) -> WorkflowRun | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_runs WHERE thread_id = ? "
            "AND status IN ('queued', 'running', 'waiting') "
            "ORDER BY created_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list_incomplete(self) -> list[WorkflowRun]:
        rows = self.connection.execute(
            "SELECT * FROM workflow_runs WHERE status IN ('queued', 'running', 'waiting') "
            "ORDER BY created_at"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorkflowRun:
        error_code = row["error_code"]
        if row["status"] == WorkflowStatus.FAILED.value and not error_code:
            error_code = "WORKFLOW_FAILED"
        return WorkflowRun(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            kind=row["kind"],
            status=WorkflowStatus(row["status"]),
            input=load_json(row["input_json"]),
            result=load_json(row["result_json"]),
            attempt=row["attempt"],
            retry_of=row["retry_of"],
            current_stage=row["current_stage"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            checkpoint=load_json(row["checkpoint_json"]),
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
            started_at=load_datetime(row["started_at"]),
            completed_at=load_datetime(row["completed_at"]),
            error_code=error_code,
            error_message=row["error_message"],
        )


class ArtifactRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, artifact: Artifact) -> None:
        self.connection.execute(
            "INSERT INTO artifacts (id, thread_id, turn_id, workflow_run_id, kind, "
            "name, media_type, storage_path, byte_size, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.id,
                artifact.thread_id,
                artifact.turn_id,
                artifact.workflow_run_id,
                artifact.kind,
                artifact.name,
                artifact.media_type,
                artifact.storage_path,
                artifact.byte_size,
                dump_json(artifact.metadata),
                dump_datetime(artifact.created_at),
            ),
        )

    def get(self, artifact_id: str) -> Artifact | None:
        row = self.connection.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list_for_thread(self, thread_id: str, *, limit: int = 200) -> list[Artifact]:
        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE thread_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_workflow(self, run_id: str) -> list[Artifact]:
        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE workflow_run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            workflow_run_id=row["workflow_run_id"],
            kind=row["kind"],
            name=row["name"],
            media_type=row["media_type"],
            storage_path=row["storage_path"],
            byte_size=row["byte_size"],
            metadata=load_json(row["metadata_json"]),
            created_at=load_required_datetime(row["created_at"]),
        )
