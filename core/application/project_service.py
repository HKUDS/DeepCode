"""Project use cases and filesystem trust-boundary checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
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

# A folder with more subfolders than this is listed only in part.
DIRECTORY_LIST_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """The subfolders of one folder on the machine running the service."""

    path: str
    parent: str | None
    home: str
    folders: tuple[str, ...]
    truncated: bool


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
    def list_directories(path: str | None = None) -> DirectoryListing:
        """List the visible subfolders of ``path`` (the home folder by default).

        This lets a client that cannot show a native folder picker, such as
        the Web client, browse the service machine for a project folder. Only
        folder names are returned, never file names or contents.
        """
        home = Path.home()
        raw = (path or "").strip()
        candidate = Path(raw).expanduser() if raw else home
        if not candidate.is_absolute():
            raise InvalidArgumentError(f"folder path must be absolute: {raw}")
        try:
            folder = candidate.resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(f"folder does not exist: {raw}") from exc
        if not folder.is_dir():
            raise InvalidArgumentError(f"not a folder: {raw}")
        names: list[str] = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            names.append(entry.name)
                    except OSError:
                        continue
        except OSError as exc:
            raise InvalidArgumentError(f"folder cannot be read: {folder}") from exc
        names.sort(key=str.casefold)
        parent = folder.parent
        return DirectoryListing(
            path=str(folder),
            parent=None if parent == folder else str(parent),
            home=str(home),
            folders=tuple(names[:DIRECTORY_LIST_LIMIT]),
            truncated=len(names) > DIRECTORY_LIST_LIMIT,
        )

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
