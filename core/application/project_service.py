"""Project use cases and filesystem trust-boundary checks."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    ProjectNotFoundError,
    ProjectNotTrustedError,
)
from core.domain.common import JsonObject, utc_now
from core.domain.project import Project, TrustState
from core.harness.hooks.trust import ProjectHookApproval, declaration_digest
from core.persistence.database import Database
from core.persistence.errors import PersistenceConflictError
from core.persistence.project_repository import ProjectRepository

# A folder with more subfolders than this is listed only in part.
DIRECTORY_LIST_LIMIT = 1000

# Project.settings key holding the digest of the project hook declarations the
# user approved (see core.harness.hooks.trust). Only this service writes it.
HOOKS_DIGEST_SETTING = "approvedHooksDigest"


def approved_hooks_digest(project: Project) -> str | None:
    """The stored approved project hook digest, if one was recorded."""
    value = project.settings.get(HOOKS_DIGEST_SETTING)
    return value if isinstance(value, str) and value else None


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
            settings=(
                {HOOKS_DIGEST_SETTING: declaration_digest(str(canonical))}
                if trust_state is TrustState.TRUSTED
                else {}
            ),
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
            next_trust = trust_state or current.trust_state
            next_settings = dict(current.settings if settings is None else settings)
            # The approved hook digest is not a client setting: a settings
            # update keeps the stored one and cannot set its own.
            next_settings.pop(HOOKS_DIGEST_SETTING, None)
            stored_digest = approved_hooks_digest(current)
            if (
                next_trust is TrustState.TRUSTED
                and current.trust_state is not TrustState.TRUSTED
            ):
                # Trusting a folder approves the hooks it declares right now.
                stored_digest = declaration_digest(current.canonical_path)
            if stored_digest is not None:
                next_settings[HOOKS_DIGEST_SETTING] = stored_digest
            updated = replace(
                current,
                display_name=name,
                trust_state=next_trust,
                settings=next_settings,
                updated_at=utc_now(),
            )
            repository.update(updated)
        return updated

    def approve_hooks(self, project_id: str, digest: str) -> Project:
        """Approve the project hook declarations whose digest is ``digest``.

        ``digest`` is the one the user reviewed in the hooks list. If the
        declarations changed since, approving would run hooks nobody saw, so
        the call is refused and the list has to be read again. An untrusted
        project is refused too: its hooks cannot run until it is trusted, and
        trusting it approves the hooks it declares then.
        """
        with self.database.transaction() as connection:
            repository = ProjectRepository(connection)
            current = repository.get(project_id)
            if current is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            if current.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must be trusted before its hooks can be approved",
                    user_message=(
                        "This project is not trusted, so its hooks cannot "
                        "run. Trust the project first; trusting it approves "
                        "the hooks it declares at that moment."
                    ),
                    details={"projectId": project_id},
                )
            actual = declaration_digest(current.canonical_path)
            if digest != actual:
                raise ConflictError(
                    "project hook declarations changed since they were listed",
                    user_message=(
                        "This project's hooks changed since the list was "
                        "loaded. Reload the hooks list, review them, and "
                        "approve again."
                    ),
                    details={"projectId": project_id, "digest": actual},
                )
            updated = replace(
                current,
                settings={**current.settings, HOOKS_DIGEST_SETTING: actual},
                updated_at=utc_now(),
            )
            repository.update(updated)
        return updated

    def hook_approval(self, project_id: str) -> ProjectHookApproval:
        """The project hook approval a Turn's agent runs under.

        A trusted Project with no stored digest adopts the hooks it declares
        now, once. Projects trusted before hook approval existed have no
        stored digest, and their hooks ran on every Turn; skipping them after
        an upgrade would break those users for no new protection, since
        nothing changed. A Project trusted since then got its digest when it
        was trusted (see :meth:`update`). Either way the recorded digest then
        stands: a later change to the declarations skips the project's hooks
        until the user approves the new ones.
        """
        project = self.read(project_id)
        stored = approved_hooks_digest(project)
        if stored is not None or project.trust_state is not TrustState.TRUSTED:
            return ProjectHookApproval(stored)
        with self.database.transaction() as connection:
            repository = ProjectRepository(connection)
            current = repository.get(project_id)
            if current is None:
                raise ProjectNotFoundError(f"project not found: {project_id}")
            stored = approved_hooks_digest(current)
            if stored is None and current.trust_state is TrustState.TRUSTED:
                stored = declaration_digest(current.canonical_path)
                repository.update(
                    replace(
                        current,
                        settings={**current.settings, HOOKS_DIGEST_SETTING: stored},
                        updated_at=utc_now(),
                    )
                )
        return ProjectHookApproval(stored)

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
