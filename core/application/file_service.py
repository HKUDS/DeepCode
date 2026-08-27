"""Bounded, symlink-safe workspace browsing and optimistic small-file edits."""

from __future__ import annotations

import codecs
import hashlib
import itertools
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.application.errors import (
    BinaryFileError,
    ConflictError,
    FileChangedError,
    FileNotFoundApplicationError,
    FileTooLargeError,
    InvalidArgumentError,
)
from core.application.workspace_service import WorkspaceService
from core.persistence.database import Database
from core.persistence.execution_repository import TurnRepository

DEFAULT_READ_LIMIT = 128 * 1024
MAX_READ_LIMIT = 128 * 1024
MAX_EDIT_BYTES = 128 * 1024
MAX_TREE_ENTRIES = 750


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    name: str
    kind: str
    size: int | None
    modified_at: str | None
    hidden: bool


@dataclass(frozen=True, slots=True)
class FileContent:
    path: str
    content: str
    byte_size: int
    sha256: str | None
    line_count: int
    truncated: bool


class FileService:
    def __init__(self, database: Database, workspaces: WorkspaceService) -> None:
        self.database = database
        self.workspaces = workspaces

    def list(
        self,
        thread_id: str,
        *,
        path: str = "",
        depth: int = 2,
        limit: int = 750,
    ) -> tuple[list[FileEntry], bool]:
        if not 1 <= depth <= 8:
            raise InvalidArgumentError("depth must be between 1 and 8")
        if not 1 <= limit <= MAX_TREE_ENTRIES:
            raise InvalidArgumentError(
                f"limit must be between 1 and {MAX_TREE_ENTRIES}"
            )
        context = self.workspaces.resolve(thread_id)
        root = self.workspaces.path(context, path, allow_root=True)
        if not root.is_dir():
            raise InvalidArgumentError("file/list path must be a directory")
        entries: list[FileEntry] = []
        truncated = False

        def visit(directory: Path, remaining: int) -> None:
            nonlocal truncated
            if truncated:
                return
            try:
                remaining_slots = limit - len(entries)
                with os.scandir(directory) as scan:
                    children = sorted(
                        itertools.islice(
                            (entry for entry in scan if entry.name != ".git"),
                            remaining_slots + 1,
                        ),
                        key=lambda entry: (
                            not entry.is_dir(follow_symlinks=False),
                            entry.name.lower(),
                        ),
                    )
            except OSError:
                return
            for child in children:
                if len(entries) >= limit:
                    truncated = True
                    return
                if len(entries) >= limit:
                    truncated = True
                    return
                try:
                    stat = child.stat(follow_symlinks=False)
                except OSError:
                    stat = None
                kind = (
                    "symlink"
                    if child.is_symlink()
                    else "directory"
                    if child.is_dir(follow_symlinks=False)
                    else "file"
                )
                child_path = Path(child.path)
                entries.append(
                    FileEntry(
                        path=child_path.relative_to(context.root).as_posix(),
                        name=child.name,
                        kind=kind,
                        size=stat.st_size
                        if stat is not None and kind == "file"
                        else None,
                        modified_at=_timestamp(stat.st_mtime)
                        if stat is not None
                        else None,
                        hidden=child.name.startswith("."),
                    )
                )
                if kind == "directory" and remaining > 1:
                    visit(child_path, remaining - 1)

        visit(root, depth)
        return entries, truncated

    def read(
        self, thread_id: str, path: str, *, max_bytes: int = DEFAULT_READ_LIMIT
    ) -> FileContent:
        if not 1 <= max_bytes <= MAX_READ_LIMIT:
            raise InvalidArgumentError(
                f"maxBytes must be between 1 and {MAX_READ_LIMIT}"
            )
        context = self.workspaces.resolve(thread_id)
        file_path = self.workspaces.path(context, path)
        if not file_path.is_file():
            raise FileNotFoundApplicationError(f"file not found: {path}")
        with file_path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        visible = raw[:max_bytes]
        if b"\x00" in visible:
            raise BinaryFileError(f"binary file cannot be displayed as text: {path}")
        try:
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            content = decoder.decode(visible, final=not truncated)
        except UnicodeDecodeError as exc:
            raise BinaryFileError(f"file is not valid UTF-8 text: {path}") from exc
        return FileContent(
            path=file_path.relative_to(context.root).as_posix(),
            content=content,
            byte_size=file_path.stat().st_size,
            sha256=None if truncated else _sha256(file_path),
            line_count=content.count("\n") + (1 if content else 0),
            truncated=truncated,
        )

    def write(
        self,
        thread_id: str,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> FileContent:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_EDIT_BYTES:
            raise FileTooLargeError(
                f"desktop edits are limited to {MAX_EDIT_BYTES} bytes"
            )
        context = self.workspaces.resolve(thread_id, require_trusted=True)
        with self.database.read() as connection:
            if TurnRepository(connection).active_for_thread(thread_id) is not None:
                raise ConflictError(
                    "cannot edit a file while the thread turn is active"
                )
        file_path = self.workspaces.path(context, path, must_exist=False)
        existing = file_path.exists()
        if existing and not file_path.is_file():
            raise InvalidArgumentError("file/write path must be a regular file")
        if existing:
            current_hash = _sha256(file_path)
            if expected_sha256 is None:
                raise FileChangedError(
                    "expectedSha256 is required for an existing file"
                )
            if current_hash != expected_sha256:
                raise FileChangedError(
                    "file changed since it was read",
                    details={"expected": expected_sha256, "actual": current_hash},
                )
        elif expected_sha256 is not None:
            raise FileChangedError("expected file no longer exists")

        mode = file_path.stat().st_mode if existing else 0o644
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=file_path.parent, prefix=".deepcode-edit-", delete=False
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, mode)
            if existing and _sha256(file_path) != current_hash:
                raise FileChangedError("file changed while the edit was being saved")
            os.replace(temporary_name, file_path)
            _fsync_directory(file_path.parent)
        finally:
            if temporary_name is not None and Path(temporary_name).exists():
                Path(temporary_name).unlink()
        return self.read(thread_id, path, max_bytes=MAX_READ_LIMIT)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
