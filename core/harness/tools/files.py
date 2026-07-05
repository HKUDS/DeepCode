"""Native read / write / edit file tools (P2).

Built on the kernel's ``Tool`` base class. Paths resolve against a workspace
root; write and edit refuse to escape it (defense in depth on top of the P1
permission seam). Edit uses the nine-strategy fuzzy replacer so exact-string
edits survive whitespace/indentation drift.

Tools return errors as data (an ``"Error: ..."`` string) rather than raising,
matching the kernel contract so a bad edit never aborts the loop.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.tools.replace import ReplaceError, replace

# Read limits (mirror the proven opencode defaults).
_DEFAULT_READ_LIMIT = 2000
_MAX_LINE_CHARS = 2000
_MAX_READ_BYTES = 256 * 1024


def _resolve(workspace: str, file_path: str) -> Path:
    base = Path(workspace)
    p = Path(os.path.expanduser(file_path))
    return (p if p.is_absolute() else base / p).resolve()


def _within(workspace: str, target: Path) -> bool:
    try:
        target.relative_to(Path(workspace).resolve())
        return True
    except ValueError:
        return False


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file (relative to the workspace or absolute).",
            },
            "offset": {
                "type": "integer",
                "description": "1-indexed line to start reading from (default 1).",
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines to read (default {_DEFAULT_READ_LIMIT}).",
            },
        },
        "required": ["file_path"],
    }
)
class ReadTool(Tool):
    """Read a file, returning line-numbered content (``<lineno>: <text>``)."""

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return (
            "Read a file and return its contents with line numbers "
            "(`<lineno>: <text>`). Supports offset/limit for large files; "
            "long lines are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        file_path = kwargs.get("file_path", "")
        offset = max(1, int(kwargs.get("offset") or 1))
        limit = int(kwargs.get("limit") or _DEFAULT_READ_LIMIT)
        target = _resolve(self._workspace, file_path)
        if not target.exists():
            return f"Error: file not found: {file_path}"
        if target.is_dir():
            entries = sorted(
                p.name + ("/" if p.is_dir() else "") for p in target.iterdir()
            )
            return f"Directory {file_path}:\n" + "\n".join(entries)
        try:
            raw = target.read_bytes()[:_MAX_READ_BYTES]
            text = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            return f"Error: could not read {file_path}: {exc}"

        lines = text.split("\n")
        end = min(len(lines), offset - 1 + limit)
        out: list[str] = []
        for i in range(offset - 1, end):
            line = lines[i]
            if len(line) > _MAX_LINE_CHARS:
                line = line[:_MAX_LINE_CHARS] + "… [truncated]"
            out.append(f"{i + 1}: {line}")
        result = "\n".join(out)
        if end < len(lines):
            result += f"\n… [{len(lines) - end} more lines; use offset/limit]"
        return result or "(empty file)"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to write (relative to the workspace or absolute).",
            },
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["file_path", "content"],
    }
)
class WriteTool(Tool):
    """Create or overwrite a file with the given content."""

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file, creating parent directories as needed. "
            "Overwrites an existing file. Use edit for targeted changes."
        )

    async def execute(self, **kwargs: Any) -> Any:
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")
        target = _resolve(self._workspace, file_path)
        if not _within(self._workspace, target):
            return (
                f"Error: refusing to write outside the workspace: {file_path}. "
                "Writes are fenced to the workspace directory."
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"Error: could not write {file_path}: {exc}"
        return f"Wrote {len(content)} bytes to {file_path}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to replace (include enough surrounding context to be unique).",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text (must differ from old_string).",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence (default false).",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }
)
class EditTool(Tool):
    """Replace ``old_string`` with ``new_string`` using fuzzy matching."""

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_string with new_string. Matching is "
            "resilient to whitespace/indentation drift; provide enough context "
            "for old_string to be unique, or set replace_all."
        )

    async def execute(self, **kwargs: Any) -> Any:
        file_path = kwargs.get("file_path", "")
        old_string = kwargs.get("old_string", "")
        new_string = kwargs.get("new_string", "")
        replace_all = bool(kwargs.get("replace_all", False))

        target = _resolve(self._workspace, file_path)
        if not _within(self._workspace, target):
            return (
                f"Error: refusing to edit outside the workspace: {file_path}. "
                "Edits are fenced to the workspace directory."
            )
        if not target.exists():
            return (
                f"Error: file not found: {file_path}. Use write to create a new file."
            )
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error: could not read {file_path}: {exc}"

        try:
            updated = replace(content, old_string, new_string, replace_all)
        except ReplaceError as exc:
            return f"Error: {exc}"
        except ValueError as exc:
            return f"Error: {exc}"

        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return f"Error: could not write {file_path}: {exc}"

        occurrences = 1
        if replace_all:
            # Report how many were changed for transparency.
            occurrences = content.count(old_string) or "multiple"
        return f"Edited {file_path} ({occurrences} replacement(s))."
