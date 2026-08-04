"""Cross-process-safe local Skill management shared by every frontend."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.skills.catalog import SkillCatalog, validate_skill_directory
from core.skills.models import (
    SkillRecord,
    SkillResolutionError,
    SkillScope,
    SkillSourceRoot,
    SkillValidationError,
)
from core.skills.runtime import SkillRuntime
from core.skills.roots import managed_skill_root

MAX_IMPORT_FILES = 500
MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_TOTAL_BYTES = 10 * 1024 * 1024


class LocalSkillManager:
    """Manage one workspace's project and user Skill catalog."""

    _thread_lock = threading.RLock()

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.runtime = SkillRuntime(self.workspace)

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        return self.runtime.catalog(force=force)

    def find(self, identifier: str) -> SkillRecord:
        clean = identifier.strip()
        if not clean:
            raise SkillResolutionError("Skill ID must not be empty")
        catalog = self.catalog()
        record = catalog.get(clean)
        if record is None and not clean.startswith("sk_"):
            # Read-only compatibility with the original name-based API.
            record = catalog.get_active(clean)
        if record is None:
            raise SkillResolutionError(f"Skill not found: {clean}")
        return record

    def select(self, identifier: str) -> SkillRecord:
        """Resolve a Turn selection without guessing between duplicate names."""

        clean = identifier.strip()
        if not clean:
            raise SkillResolutionError("Skill ID or name must not be empty")
        catalog = self.catalog()
        if clean.startswith("sk_"):
            return catalog.select_id(clean)
        return catalog.select_name(clean)

    def set_enabled(
        self,
        skill_id: str,
        *,
        enabled: bool,
        config_scope: SkillScope,
    ) -> SkillCatalog:
        if self.runtime.catalog().get(skill_id) is None:
            raise SkillResolutionError(f"Skill not found: {skill_id}")
        self.runtime.policy.set_enabled(
            skill_id,
            enabled=enabled,
            scope=config_scope,
        )
        self.runtime.invalidate()
        return self.runtime.catalog(force=True)

    def import_directory(
        self,
        source: str | Path,
        *,
        target_scope: SkillScope,
    ) -> SkillRecord:
        unresolved_source = Path(source).expanduser()
        if unresolved_source.is_symlink():
            raise SkillValidationError("Skill source must not be a symlink")
        source_path = unresolved_source.resolve(strict=True)
        _validate_import_tree(source_path)
        candidate = validate_skill_directory(source_path)
        target_root = self.managed_root(target_scope)
        destination = target_root / candidate.name
        if target_root.resolve(strict=False).is_relative_to(source_path):
            raise SkillValidationError(
                "Skill source may not contain its managed import destination"
            )
        lock_path = target_root.parent / "skills.lock"
        with self._thread_lock, _file_lock(lock_path):
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    f"a managed Skill named {candidate.name!r} already exists"
                )
            target_root.mkdir(parents=True, exist_ok=True)
            temporary = target_root / f".{candidate.name}.{uuid.uuid4().hex}.tmp"
            try:
                # Preserve links during the copy so the second validation can
                # reject them; following a link here could copy arbitrary data.
                shutil.copytree(source_path, temporary, symlinks=True)
                _validate_import_tree(temporary)
                validate_skill_directory(temporary)
                os.replace(temporary, destination)
                _fsync_directory(target_root)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)

        self.runtime.invalidate()
        imported = next(
            (
                record
                for record in self.catalog(force=True).records
                if record.source_root is SkillSourceRoot.AGENTS
                and record.scope is target_scope
                and record.key.relative_path == candidate.name
            ),
            None,
        )
        if imported is None:
            raise RuntimeError("imported Skill was not discoverable")
        return imported

    def delete(self, skill_id: str) -> SkillRecord:
        record = self.find(skill_id)
        if not record.deletable:
            raise PermissionError(
                "Compatibility and system Skills are read-only; remove legacy "
                "Skills from their source directory"
            )
        target_root = self.managed_root(record.scope)
        directory = Path(record.directory).resolve(strict=False)
        if directory.parent != target_root.resolve(strict=False):
            raise SkillValidationError("Skill is outside the managed root")
        lock_path = target_root.parent / "skills.lock"
        with self._thread_lock, _file_lock(lock_path):
            if not directory.exists():
                raise SkillResolutionError(f"Skill no longer exists: {skill_id}")
            if directory.is_symlink() or not directory.is_dir():
                raise SkillValidationError("managed Skill path is not a directory")
            tombstone = directory.with_name(
                f".{directory.name}.deleted.{uuid.uuid4().hex}"
            )
            os.replace(directory, tombstone)
            _fsync_directory(target_root)
            shutil.rmtree(tombstone)
        self.runtime.invalidate()
        return record

    def managed_root(self, scope: SkillScope) -> Path:
        return managed_skill_root(self.workspace, scope)


def _validate_import_tree(source: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise SkillValidationError("Skill source must be a real directory")
    file_count = 0
    total_bytes = 0
    for path in source.rglob("*"):
        if path.is_symlink():
            raise SkillValidationError(
                f"Skill imports may not contain symlinks: {path}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SkillValidationError(f"unsupported Skill resource: {path}")
        file_count += 1
        if file_count > MAX_IMPORT_FILES:
            raise SkillValidationError(f"Skill import exceeds {MAX_IMPORT_FILES} files")
        size = path.stat().st_size
        if size > MAX_IMPORT_FILE_BYTES:
            raise SkillValidationError(
                f"Skill resource exceeds {MAX_IMPORT_FILE_BYTES} bytes: {path.name}"
            )
        total_bytes += size
        if total_bytes > MAX_IMPORT_TOTAL_BYTES:
            raise SkillValidationError(
                f"Skill import exceeds {MAX_IMPORT_TOTAL_BYTES} total bytes"
            )


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.path.getsize(path) == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "LocalSkillManager",
    "MAX_IMPORT_FILES",
    "MAX_IMPORT_FILE_BYTES",
    "MAX_IMPORT_TOTAL_BYTES",
]
