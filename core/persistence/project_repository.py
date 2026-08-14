"""Project persistence mapping."""

from __future__ import annotations

import sqlite3

from core.domain.project import Project, TrustState
from core.persistence.errors import PersistenceConflictError
from core.persistence.serde import (
    dump_datetime,
    dump_json,
    load_json,
    load_required_datetime,
)


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, project: Project) -> None:
        try:
            self.connection.execute(
                "INSERT INTO projects (id, canonical_path, display_name, trust_state, "
                "settings_json, created_at, updated_at, last_opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.canonical_path,
                    project.display_name,
                    project.trust_state.value,
                    dump_json(project.settings),
                    dump_datetime(project.created_at),
                    dump_datetime(project.updated_at),
                    dump_datetime(project.last_opened_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceConflictError("project insert conflicted") from exc

    def update(self, project: Project) -> None:
        cursor = self.connection.execute(
            "UPDATE projects SET display_name = ?, trust_state = ?, settings_json = ?, "
            "updated_at = ?, last_opened_at = ? WHERE id = ?",
            (
                project.display_name,
                project.trust_state.value,
                dump_json(project.settings),
                dump_datetime(project.updated_at),
                dump_datetime(project.last_opened_at),
                project.id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(project.id)

    def get(self, project_id: str) -> Project | None:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def get_by_path(self, canonical_path: str) -> Project | None:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE canonical_path = ?", (canonical_path,)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Project]:
        rows = self.connection.execute(
            "SELECT * FROM projects ORDER BY last_opened_at DESC, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def remove(self, project_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM projects WHERE id = ?", (project_id,)
        )
        return cursor.rowcount == 1

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            canonical_path=row["canonical_path"],
            display_name=row["display_name"],
            trust_state=TrustState(row["trust_state"]),
            settings=load_json(row["settings_json"]),
            created_at=load_required_datetime(row["created_at"]),
            updated_at=load_required_datetime(row["updated_at"]),
            last_opened_at=load_required_datetime(row["last_opened_at"]),
        )
