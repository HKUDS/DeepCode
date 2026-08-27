"""Read-only structured Git status and diff projections for the desktop."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from core.application.errors import (
    ConflictError,
    FileChangedError,
    GitUnavailableError,
    InvalidArgumentError,
)
from core.application.workspace_service import WorkspaceService
from core.persistence.database import Database
from core.persistence.execution_repository import TurnRepository

MAX_GIT_OUTPUT = 8 * 1024 * 1024
MAX_DIFF_FILES = 500
MAX_DIFF_LINES = 4_000
MAX_DIFF_TEXT_BYTES = 256 * 1024
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: ?(.*))?$")


@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    path: str
    original_path: str | None
    index_status: str
    worktree_status: str
    kind: str


@dataclass(frozen=True, slots=True)
class GitStatus:
    repository_root: str
    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    detached: bool
    entries: tuple[GitStatusEntry, ...]


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: str
    text: str
    old_line: int | None
    new_line: int | None


@dataclass(frozen=True, slots=True)
class DiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    heading: str
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class FileDiff:
    path: str
    original_path: str | None
    status: str
    binary: bool
    additions: int
    deletions: int
    hunks: tuple[DiffHunk, ...]
    revision: str = ""


class GitService:
    def __init__(self, database: Database, workspaces: WorkspaceService) -> None:
        self.database = database
        self.workspaces = workspaces

    def status(self, thread_id: str) -> GitStatus:
        context = self.workspaces.resolve(thread_id)
        repository_root = self.repository_root(context.root)
        completed = self._run_bytes(
            context.root,
            "status",
            "--porcelain=v1",
            "-z",
            "--branch",
            "--untracked-files=all",
        )
        branch: str | None = None
        upstream: str | None = None
        ahead = behind = 0
        detached = False
        entries: list[GitStatusEntry] = []
        records = completed.stdout.split(b"\0")
        index = 0
        while index < len(records):
            raw = records[index]
            index += 1
            if not raw:
                continue
            if raw.startswith(b"## "):
                branch, upstream, ahead, behind, detached = _parse_branch_header(
                    raw[3:].decode("utf-8", errors="replace")
                )
                continue
            if len(raw) < 4:
                continue
            xy = raw[:2].decode("ascii", errors="replace")
            path = raw[3:].decode("utf-8", errors="replace")
            original: str | None = None
            if xy[0] in {"R", "C"} and index < len(records):
                original = records[index].decode("utf-8", errors="replace")
                index += 1
            entries.append(
                GitStatusEntry(
                    path=path,
                    original_path=original,
                    index_status=xy[0],
                    worktree_status=xy[1],
                    kind=_status_kind(xy),
                )
            )
            if len(entries) > MAX_DIFF_FILES:
                raise InvalidArgumentError(
                    f"Git status contains more than {MAX_DIFF_FILES} changed files"
                )
        return GitStatus(
            repository_root=str(repository_root),
            branch=branch,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            detached=detached,
            entries=tuple(entries),
        )

    def diff(
        self,
        thread_id: str,
        *,
        scope: str = "all",
        path: str | None = None,
    ) -> tuple[FileDiff, ...]:
        if scope not in {"all", "staged", "working"}:
            raise InvalidArgumentError("scope must be all, staged, or working")
        context = self.workspaces.resolve(thread_id)
        status = self.status(thread_id)
        selected_path: str | None = None
        if path is not None:
            selected = self.workspaces.path(context, path, must_exist=False)
            selected_path = selected.relative_to(context.root).as_posix()
        candidates = [
            entry
            for entry in status.entries
            if entry.kind != "ignored"
            and (selected_path is None or entry.path == selected_path)
            and _entry_in_scope(entry, scope)
        ]
        if len(candidates) > MAX_DIFF_FILES:
            raise InvalidArgumentError(
                f"diff contains more than {MAX_DIFF_FILES} changed files"
            )
        files: list[FileDiff] = []
        total_bytes = 0
        total_lines = 0
        total_text_bytes = 0
        has_head = self._has_head(context.root)
        for entry in candidates:
            if entry.kind == "untracked":
                file_diff = self._new_file_diff(context.root, entry, "untracked")
            elif scope == "all" and not has_head:
                file_diff = self._new_file_diff(context.root, entry, entry.kind)
            else:
                arguments = [
                    "diff",
                    "--no-ext-diff",
                    "--no-color",
                    "--find-renames",
                    "--unified=3",
                ]
                if scope == "staged":
                    arguments.append("--cached")
                elif scope == "all":
                    arguments.append("HEAD")
                paths = (
                    [entry.original_path, entry.path]
                    if entry.original_path is not None
                    else [entry.path]
                )
                arguments.extend(("--", *paths))
                patch = self._run_bytes(context.root, *arguments).stdout
                total_bytes += len(patch)
                if total_bytes > MAX_GIT_OUTPUT:
                    raise InvalidArgumentError(
                        "structured diff exceeds the output limit"
                    )
                file_diff = _attach_revision(_parse_file_patch(entry, patch), patch)
            total_lines += sum(len(hunk.lines) for hunk in file_diff.hunks)
            total_text_bytes += sum(
                len(line.text.encode("utf-8"))
                for hunk in file_diff.hunks
                for line in hunk.lines
            )
            if total_lines > MAX_DIFF_LINES or total_text_bytes > MAX_DIFF_TEXT_BYTES:
                raise InvalidArgumentError(
                    "structured diff exceeds the display-safe limit; select one file"
                )
            files.append(file_diff)
        return tuple(files)

    def discard(self, thread_id: str, path: str, expected_revision: str) -> str:
        context = self.workspaces.resolve(thread_id, require_trusted=True)
        with self.database.read() as connection:
            if TurnRepository(connection).active_for_thread(thread_id) is not None:
                raise ConflictError(
                    "cannot discard changes while the thread Turn is active"
                )
        current = self.diff(thread_id, scope="all", path=path)
        if len(current) != 1:
            raise FileChangedError("the selected change no longer exists")
        file_diff = current[0]
        if file_diff.revision != expected_revision:
            raise FileChangedError(
                "the selected change changed since it was reviewed",
                details={
                    "expected": expected_revision,
                    "actual": file_diff.revision,
                },
            )
        selected = self.workspaces.path(context, file_diff.path, must_exist=False)
        if file_diff.status == "untracked":
            if selected.is_symlink() or selected.is_file():
                selected.unlink()
            else:
                raise FileChangedError("the untracked file is no longer discardable")
            return file_diff.path

        paths = (
            [file_diff.original_path, file_diff.path]
            if file_diff.original_path is not None
            else [file_diff.path]
        )
        if not self._has_head(context.root):
            self._run_bytes(context.root, "rm", "--cached", "--", *paths)
            if selected.is_symlink() or selected.is_file():
                selected.unlink()
            return file_diff.path
        self._run_bytes(
            context.root,
            "restore",
            "--source=HEAD",
            "--staged",
            "--worktree",
            "--",
            *paths,
        )
        return file_diff.path

    def repository_root(self, workspace: Path) -> Path:
        completed = self._run_bytes(workspace, "rev-parse", "--show-toplevel")
        value = completed.stdout.decode("utf-8", errors="strict").strip()
        try:
            return Path(value).resolve(strict=True)
        except OSError as exc:
            raise GitUnavailableError("Git repository root cannot be resolved") from exc

    def _has_head(self, workspace: Path) -> bool:
        return (
            self._run_bytes(
                workspace, "rev-parse", "--verify", "HEAD", check=False
            ).returncode
            == 0
        )

    def _new_file_diff(
        self, root: Path, entry: GitStatusEntry, status: str
    ) -> FileDiff:
        path = root / entry.path
        if not path.is_file() or path.is_symlink():
            material = (
                os.readlink(path).encode("utf-8", errors="surrogateescape")
                if path.is_symlink()
                else b"<missing-or-non-file>"
            )
            return _attach_revision(
                FileDiff(entry.path, entry.original_path, status, False, 0, 0, ()),
                material,
            )
        with path.open("rb") as handle:
            raw = handle.read(MAX_GIT_OUTPUT + 1)
        if len(raw) > MAX_GIT_OUTPUT:
            raise InvalidArgumentError(
                f"untracked file is too large to diff: {entry.path}"
            )
        if b"\x00" in raw:
            return _attach_revision(
                FileDiff(entry.path, entry.original_path, status, True, 0, 0, ()),
                raw,
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _attach_revision(
                FileDiff(entry.path, entry.original_path, status, True, 0, 0, ()),
                raw,
            )
        source_lines = text.splitlines()
        lines = tuple(
            DiffLine(kind="addition", text=line, old_line=None, new_line=index)
            for index, line in enumerate(source_lines, start=1)
        )
        hunk = DiffHunk(
            old_start=0,
            old_lines=0,
            new_start=1,
            new_lines=len(source_lines),
            heading="new file",
            lines=lines,
        )
        return _attach_revision(
            FileDiff(
                path=entry.path,
                original_path=entry.original_path,
                status=status,
                binary=False,
                additions=len(source_lines),
                deletions=0,
                hunks=(hunk,),
            ),
            raw,
        )

    @staticmethod
    def _run_bytes(
        workspace: Path, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
        try:
            process = subprocess.Popen(
                ["git", *arguments],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        except OSError as exc:
            raise GitUnavailableError(f"Git command could not run: {exc}") from exc
        assert process.stdout is not None and process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()

        def drain(stream, target: bytearray, limit: int) -> None:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                remaining = limit - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass

        stdout_thread = threading.Thread(
            target=drain, args=(process.stdout, stdout, MAX_GIT_OUTPUT), daemon=True
        )
        stderr_thread = threading.Thread(
            target=drain, args=(process.stderr, stderr, 64 * 1024), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            return_code = process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise GitUnavailableError("Git command timed out") from exc
        finally:
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
        if overflow.is_set():
            raise InvalidArgumentError("Git output exceeds the configured limit")
        completed = subprocess.CompletedProcess(
            ["git", *arguments], return_code, bytes(stdout), bytes(stderr)
        )
        if check and return_code != 0:
            detail = bytes(stderr).decode("utf-8", errors="replace").strip()
            raise GitUnavailableError(detail or f"git {arguments[0]} failed")
        return completed


def _attach_revision(file_diff: FileDiff, material: bytes) -> FileDiff:
    digest = hashlib.sha256()
    for value in (
        file_diff.path,
        file_diff.original_path or "",
        file_diff.status,
        "binary" if file_diff.binary else "text",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(material)
    return replace(file_diff, revision=digest.hexdigest())


def _parse_branch_header(
    header: str,
) -> tuple[str | None, str | None, int, int, bool]:
    if header.startswith("No commits yet on "):
        return header.removeprefix("No commits yet on "), None, 0, 0, False
    if header.startswith("HEAD (no branch)") or header.startswith("HEAD detached"):
        return None, None, 0, 0, True
    head, _, tracking = header.partition("...")
    upstream: str | None = None
    ahead = behind = 0
    if tracking:
        upstream = tracking.split(" ", 1)[0]
    match = re.search(r"\[(.*?)\]", header)
    if match:
        for part in match.group(1).split(", "):
            if part.startswith("ahead "):
                ahead = int(part.removeprefix("ahead "))
            elif part.startswith("behind "):
                behind = int(part.removeprefix("behind "))
    return head, upstream, ahead, behind, False


def _status_kind(xy: str) -> str:
    if xy == "??":
        return "untracked"
    if xy == "!!":
        return "ignored"
    if xy in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
        return "conflicted"
    code = xy[0] if xy[0] != " " else xy[1]
    return {
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
        "M": "modified",
    }.get(code, "changed")


def _entry_in_scope(entry: GitStatusEntry, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "staged":
        return entry.index_status not in {" ", "?", "!"}
    return entry.worktree_status not in {" ", "!"} or entry.kind == "untracked"


def _parse_file_patch(entry: GitStatusEntry, raw: bytes) -> FileDiff:
    text = raw.decode("utf-8", errors="replace")
    binary = "GIT binary patch" in text or "Binary files " in text
    hunks: list[DiffHunk] = []
    current_header: tuple[int, int, int, int, str] | None = None
    current_lines: list[DiffLine] = []
    old_line = new_line = 0

    def flush() -> None:
        nonlocal current_header, current_lines
        if current_header is None:
            return
        old_start, old_count, new_start, new_count, heading = current_header
        hunks.append(
            DiffHunk(
                old_start,
                old_count,
                new_start,
                new_count,
                heading,
                tuple(current_lines),
            )
        )
        current_header = None
        current_lines = []

    for line in text.splitlines():
        match = _HUNK_HEADER.match(line)
        if match:
            flush()
            old_line = int(match.group(1))
            new_line = int(match.group(3))
            current_header = (
                old_line,
                int(match.group(2) or "1"),
                new_line,
                int(match.group(4) or "1"),
                match.group(5) or "",
            )
            continue
        if current_header is None:
            continue
        if line.startswith("+"):
            current_lines.append(DiffLine("addition", line[1:], None, new_line))
            new_line += 1
        elif line.startswith("-"):
            current_lines.append(DiffLine("deletion", line[1:], old_line, None))
            old_line += 1
        elif line.startswith(" "):
            current_lines.append(DiffLine("context", line[1:], old_line, new_line))
            old_line += 1
            new_line += 1
        else:
            current_lines.append(DiffLine("meta", line, None, None))
    flush()
    additions = sum(line.kind == "addition" for hunk in hunks for line in hunk.lines)
    deletions = sum(line.kind == "deletion" for hunk in hunks for line in hunk.lines)
    return FileDiff(
        path=entry.path,
        original_path=entry.original_path,
        status=entry.kind,
        binary=binary,
        additions=additions,
        deletions=deletions,
        hunks=tuple(hunks),
    )
