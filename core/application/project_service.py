"""Project use cases and filesystem trust-boundary checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.application.errors import (
    InvalidArgumentError,
    ProjectNotFoundError,
)
from core.domain.common import JsonObject, utc_now
from core.domain.project import Project, TrustState
from core.persistence.database import Database
from core.persistence.errors import PersistenceConflictError
from core.persistence.project_repository import ProjectRepository


class ProjectService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(
        self,
        path: str,
        *,
        display_name: str | None = None,
        trust_state: TrustState = TrustState.UNTRUSTED,
    ) -> Project:
        canonical = self._canonical_directory(path)
        name = (display_name or canonical.name).strip()
        if not name:
            raise InvalidArgumentError("project display name must not be empty")
        now = utc_now()
        project = Project(
            canonical_path=str(canonical),
            display_name=name,
            trust_state=trust_state,
            created_at=now,
            updated_at=now,
            last_opened_at=now,
        )
        try:
            with self.database.transaction() as connection:
                repository = ProjectRepository(connection)
                existing = repository.get_by_path(project.canonical_path)
                if existing is not None:
                    opened = replace(existing, last_opened_at=now, updated_at=now)
                    repository.update(opened)
                    return opened
                repository.add(project)
        except PersistenceConflictError as exc:
            raise InvalidArgumentError("project could not be added") from exc
        return project

    def read(self, project_id: str) -> Project:
        with self.database.read() as connection:
            project = ProjectRepository(connection).get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project not found: {project_id}")
        return project

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Project]:
        self._validate_page(limit, offset)
        with self.database.read() as connection:
            return ProjectRepository(connection).list(limit=limit, offset=offset)

    def update(
        self,
        project_id: str,
        *,
        display_name: str | None = None,
        trust_state: TrustState | None = None,
        settings: JsonObject | None = None,
    ) -> Project:
        with self.database.transaction() as connection:
            repository = ProjectRepository(connection)
            current = repository.get(project_id)
            if current is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            name = (
                current.display_name if display_name is None else display_name.strip()
            )
            if not name:
                raise InvalidArgumentError("project display name must not be empty")
            updated = replace(
                current,
                display_name=name,
                trust_state=trust_state or current.trust_state,
                settings=dict(current.settings if settings is None else settings),
                updated_at=utc_now(),
            )
            repository.update(updated)
        return updated

    def remove(self, project_id: str) -> bool:
        with self.database.transaction() as connection:
            removed = ProjectRepository(connection).remove(project_id)
        if not removed:
            raise ProjectNotFoundError(f"project not found: {project_id}")
        return True

    @staticmethod
    def _canonical_directory(path: str) -> Path:
        if not path.strip():
            raise InvalidArgumentError("project path must not be empty")
        candidate = Path(path).expanduser()
        try:
            canonical = candidate.resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(f"project path does not exist: {path}") from exc
        if not canonical.is_dir():
            raise InvalidArgumentError(f"project path is not a directory: {path}")
        return canonical

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 500 or offset < 0:
            raise InvalidArgumentError("invalid pagination")
