"""Explicit Git worktree lifecycle owned by durable Threads."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from core.application.errors import (
    ConflictError,
    GitUnavailableError,
    ProjectNotFoundError,
    ProjectNotTrustedError,
    ThreadNotFoundError,
)
from core.application.event_service import EventBroker
from core.application.git_service import GitService
from core.application.views import thread_view
from core.domain.common import utc_now
from core.domain.project import Project, TrustState
from core.domain.thread import Thread
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.persistence.project_repository import ProjectRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore

MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class WorktreeResult:
    thread: Thread
    path: str
    branch: str
    disposition: str
    dirty: bool


class WorktreeService:
    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        git: GitService,
        sessions: SessionStore,
    ) -> None:
        self.database = database
        self.broker = broker
        self.git = git
        self.sessions = sessions
        self._terminal_activity = lambda _thread_id: False

    def set_terminal_activity_check(self, callback) -> None:
        self._terminal_activity = callback

    def create(self, thread_id: str) -> WorktreeResult:
        activity = self.sessions.acquire_activity_lease(thread_id)
        if activity is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        with activity:
            return self._create(thread_id)

    def _create(self, thread_id: str) -> WorktreeResult:
        thread, project = self._load(thread_id)
        project_root = Path(project.canonical_path).resolve(strict=True)
        base = self.git.repository_root(project_root)
        if base != project_root:
            raise ConflictError(
                "worktree isolation requires the opened Project to be the Git root",
                details={"projectPath": str(project_root), "gitRoot": str(base)},
            )
        self._git(base, "rev-parse", "--verify", "HEAD")
        branch = f"deepcode/{thread_id}"
        worktrees_root = base.parent / f".{base.name}.deepcode-worktrees"
        expected_path = (worktrees_root / thread_id).resolve(strict=False)
        path = (
            Path(thread.worktree_path).resolve(strict=False)
            if thread.worktree_path is not None
            else expected_path
        )
        if path != expected_path:
            raise ConflictError("stored worktree path is outside the managed location")
        registrations = self._registered_worktrees(base)
        registered_path = path.resolve(strict=False)
        registered = registered_path in registrations
        registered_branch = registrations.get(registered_path)
        manifest = self._manifest_path(base, thread_id)

        if path.is_dir() and (path / ".git").exists():
            self._require_manifest(manifest, thread, base, path, branch)
            if registered_branch != f"refs/heads/{branch}":
                raise ConflictError(
                    "worktree path is not registered to this Thread branch",
                    details={"path": str(path), "branch": registered_branch},
                )
            actual_branch = self._branch_at(path)
            if actual_branch != branch:
                raise ConflictError(
                    "worktree branch does not match its owning Thread",
                    details={"expected": branch, "actual": actual_branch},
                )
            return self._record_worktree(
                thread, path.resolve(strict=True), branch, "reclaimed"
            )

        self._require_idle(thread_id)
        if path.exists():
            raise ConflictError(
                "worktree path already exists and is not owned by this Thread",
                details={"path": str(path)},
            )
        if manifest.exists():
            self._require_manifest(manifest, thread, base, path, branch)

        if registered:
            if registered_branch != f"refs/heads/{branch}":
                raise ConflictError(
                    "managed worktree registration belongs to another branch",
                    details={"path": str(path), "branch": registered_branch},
                )
            self._git(base, "worktree", "prune", "--expire", "now")

        worktrees_root.mkdir(parents=True, exist_ok=True)
        branch_exists = (
            self._git(
                base,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                check=False,
            ).returncode
            == 0
        )
        if branch_exists:
            self._git(base, "worktree", "add", str(path), branch)
        else:
            self._git(base, "worktree", "add", "-b", branch, str(path), "HEAD")
        try:
            self._write_manifest(manifest, thread, base, path, branch)
        except OSError as exc:
            self._git(base, "worktree", "remove", "--force", str(path), check=False)
            raise ConflictError(
                f"worktree ownership manifest could not be saved: {exc}"
            ) from exc
        return self._record_worktree(
            thread, path.resolve(strict=True), branch, "created"
        )

    def remove(
        self,
        thread_id: str,
        *,
        disposition: str,
        force: bool = False,
        delete_branch: bool = False,
    ) -> WorktreeResult:
        if disposition not in {"keep", "clean"}:
            raise ConflictError("disposition must be keep or clean")
        thread, project = self._load(thread_id)
        if thread.worktree_path is None:
            raise ConflictError("thread does not own a worktree")
        project_root = Path(project.canonical_path).resolve(strict=True)
        base = self.git.repository_root(project_root)
        if base != project_root:
            raise ConflictError(
                "worktree isolation requires the opened Project to be the Git root",
                details={"projectPath": str(project_root), "gitRoot": str(base)},
            )
        path = Path(thread.worktree_path).resolve(strict=False)
        expected_path = (
            base.parent / f".{base.name}.deepcode-worktrees" / thread_id
        ).resolve(strict=False)
        if path != expected_path:
            raise ConflictError("stored worktree path is outside the managed location")
        expected_branch = f"deepcode/{thread_id}"
        manifest = self._manifest_path(base, thread_id)
        if not path.exists():
            if disposition == "keep":
                raise ConflictError("owned worktree no longer exists")
            self._require_idle(thread_id)
            self._git(base, "worktree", "prune", "--expire", "now")
            self._remove_manifest_if_owned(
                manifest, thread, base, path, expected_branch
            )
            updated = replace(
                thread,
                workspace_path=project.canonical_path,
                worktree_path=None,
                updated_at=utc_now(),
            )
            self._persist(updated, "thread.worktree_removed")
            if delete_branch:
                delete_flag = "-D" if force else "-d"
                self._git(base, "branch", delete_flag, expected_branch)
            return WorktreeResult(
                updated,
                str(path),
                expected_branch,
                "cleaned",
                False,
            )
        if not (path / ".git").exists():
            raise ConflictError("owned worktree path is not a Git worktree")
        self._require_manifest(manifest, thread, base, path, expected_branch)
        branch = self._branch_at(path)
        if branch != expected_branch:
            raise ConflictError(
                "worktree branch does not match its owning Thread",
                details={"expected": expected_branch, "actual": branch},
            )
        dirty = self._dirty(path)
        if disposition == "keep":
            return WorktreeResult(thread, str(path), branch, "kept", dirty)
        self._require_idle(thread_id)
        if self._terminal_activity(thread_id):
            raise ConflictError(
                "close the Thread terminal before cleaning its worktree"
            )
        if dirty and not force:
            raise ConflictError(
                "worktree has uncommitted changes; force must be explicit",
                details={"path": str(path), "dirty": True},
            )
        arguments = ["worktree", "remove"]
        if force:
            arguments.append("--force")
        arguments.append(str(path))
        self._git(base, *arguments)
        manifest.unlink(missing_ok=True)
        updated = replace(
            thread,
            workspace_path=project.canonical_path,
            worktree_path=None,
            updated_at=utc_now(),
        )
        self._persist(updated, "thread.worktree_removed")
        if delete_branch:
            delete_flag = "-D" if force else "-d"
            self._git(base, "branch", delete_flag, branch)
        return WorktreeResult(updated, str(path), branch, "cleaned", dirty)

    @staticmethod
    def _manifest_path(base: Path, thread_id: str) -> Path:
        return (
            base.parent
            / f".{base.name}.deepcode-worktrees"
            / ".deepcode-manifests"
            / f"{thread_id}.json"
        )

    @staticmethod
    def _manifest_payload(
        thread: Thread, base: Path, path: Path, branch: str
    ) -> dict[str, object]:
        return {
            "version": MANIFEST_VERSION,
            "threadId": thread.id,
            "projectId": thread.project_id,
            "repositoryRoot": str(base.resolve(strict=True)),
            "worktreePath": str(path.resolve(strict=False)),
            "branch": branch,
        }

    @classmethod
    def _require_manifest(
        cls,
        manifest: Path,
        thread: Thread,
        base: Path,
        path: Path,
        branch: str,
    ) -> None:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConflictError(
                "worktree ownership manifest is missing or invalid",
                details={"manifest": str(manifest)},
            ) from exc
        if payload != cls._manifest_payload(thread, base, path, branch):
            raise ConflictError(
                "worktree ownership manifest does not match this Thread",
                details={"manifest": str(manifest)},
            )

    @classmethod
    def _write_manifest(
        cls,
        manifest: Path,
        thread: Thread,
        base: Path,
        path: Path,
        branch: str,
    ) -> None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=manifest.parent,
                prefix=".manifest-",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(
                    cls._manifest_payload(thread, base, path, branch),
                    temporary,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, manifest)
        finally:
            if temporary_name is not None and Path(temporary_name).exists():
                Path(temporary_name).unlink()

    @classmethod
    def _remove_manifest_if_owned(
        cls,
        manifest: Path,
        thread: Thread,
        base: Path,
        path: Path,
        branch: str,
    ) -> None:
        if not manifest.exists():
            return
        cls._require_manifest(manifest, thread, base, path, branch)
        manifest.unlink()

    def _record_worktree(
        self, thread: Thread, path: Path, branch: str, disposition: str
    ) -> WorktreeResult:
        updated = replace(
            thread,
            workspace_path=str(path),
            worktree_path=str(path),
            updated_at=utc_now(),
        )
        self._persist(updated, "thread.worktree_created")
        return WorktreeResult(
            updated, str(path), branch, disposition, self._dirty(path)
        )

    def _persist(self, thread: Thread, event_type: str) -> None:
        with self.database.transaction() as connection:
            ThreadRepository(connection).update(thread)
            event = EventRepository(connection).append(
                thread_id=thread.id,
                type=event_type,
                payload={"thread": thread_view(thread)},
            )
        self.broker.publish(event)

    def _load(self, thread_id: str) -> tuple[Thread, Project]:
        with self.database.read() as connection:
            thread = ThreadRepository(connection).get(thread_id)
            if thread is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            project = ProjectRepository(connection).get(thread.project_id)
        if project is None:
            raise ProjectNotFoundError(f"project not found: {thread.project_id}")
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError("project must be trusted for worktree changes")
        return thread, project

    def _require_idle(self, thread_id: str) -> None:
        with self.database.read() as connection:
            if TurnRepository(connection).active_for_thread(thread_id) is not None:
                raise ConflictError("cannot change worktree while a Turn is active")

    @staticmethod
    def _registered_worktrees(base: Path) -> dict[Path, str | None]:
        completed = WorktreeService._git(base, "worktree", "list", "--porcelain")
        registrations: dict[Path, str | None] = {}
        current: Path | None = None
        for line in completed.stdout.splitlines():
            if line.startswith("worktree "):
                current = Path(line.removeprefix("worktree ")).resolve(strict=False)
                registrations[current] = None
            elif line.startswith("branch ") and current is not None:
                registrations[current] = line.removeprefix("branch ")
        return registrations

    @staticmethod
    def _branch_at(path: Path) -> str:
        completed = WorktreeService._git(path, "branch", "--show-current")
        branch = completed.stdout.strip()
        if not branch:
            raise ConflictError(
                "worktree is detached and cannot be managed as a Thread"
            )
        return branch

    @staticmethod
    def _dirty(path: Path) -> bool:
        return bool(WorktreeService._git(path, "status", "--porcelain").stdout.strip())

    @staticmethod
    def _git(
        cwd: Path, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitUnavailableError(f"Git worktree command failed: {exc}") from exc
        if check and completed.returncode != 0:
            raise GitUnavailableError(
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"git {arguments[0]} failed"
            )
        return completed
