"""Resolve Thread workspaces and enforce every filesystem trust boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.application.errors import (
    InvalidArgumentError,
    ProjectNotFoundError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
    WorkspaceOutOfScopeError,
)
from core.domain.project import Project, TrustState
from core.domain.thread import Thread
from core.persistence.database import Database
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    project: Project
    thread: Thread
    root: Path


class WorkspaceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def resolve(
        self, thread_id: str, *, require_trusted: bool = False
    ) -> WorkspaceContext:
        with self.database.read() as connection:
            thread = ThreadRepository(connection).get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            project = ProjectRepository(connection).get(thread.project_id)
        if project is None:
            raise ProjectNotFoundError(f"project not found: {thread.project_id}")
        if require_trusted and project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError("project must be trusted for this operation")
        try:
            root = Path(thread.workspace_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(
                f"thread workspace does not exist: {thread.workspace_path}"
            ) from exc
        if not root.is_dir():
            raise InvalidArgumentError("thread workspace must be a directory")

        project_root = Path(project.canonical_path).resolve(strict=True)
        if thread.worktree_path is None:
            if not root.is_relative_to(project_root):
                raise WorkspaceOutOfScopeError(
                    f"workspace is outside project boundary: {root}"
                )
        else:
            worktree = Path(thread.worktree_path).expanduser().resolve(strict=True)
            if root != worktree or not (worktree / ".git").exists():
                raise WorkspaceOutOfScopeError(
                    "thread worktree ownership metadata is invalid"
                )
        return WorkspaceContext(project=project, thread=thread, root=root)

    def path(
        self,
        context: WorkspaceContext,
        relative_path: str,
        *,
        must_exist: bool = True,
        allow_root: bool = False,
    ) -> Path:
        clean = relative_path.strip()
        if "\x00" in clean:
            raise InvalidArgumentError("path contains a null byte")
        relative = Path(clean or ".")
        if relative.is_absolute() or ".." in relative.parts:
            raise WorkspaceOutOfScopeError("path must stay inside the thread workspace")
        if relative == Path(".") and not allow_root:
            raise InvalidArgumentError("path must identify a workspace entry")
        candidate = context.root / relative
        try:
            resolved = (
                candidate.resolve(strict=True)
                if must_exist
                else candidate.parent.resolve(strict=True) / candidate.name
            )
        except OSError as exc:
            raise InvalidArgumentError(
                f"path cannot be resolved: {relative_path}"
            ) from exc
        if not resolved.is_relative_to(context.root):
            raise WorkspaceOutOfScopeError("resolved path leaves the thread workspace")
        return resolved
