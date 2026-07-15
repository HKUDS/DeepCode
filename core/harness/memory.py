"""Agent memory — project instructions + persistent cross-session notes (P2).

Two layers, aligned with Claude Code (DEEPCODE_V2_MASTER_PLAN.md P2-L5d(c)):

1. **Project instructions** — ``AGENTS.md`` / ``DEEPCODE.md`` / ``CLAUDE.md``
   discovered from the enclosing repo root down to the workspace (so a nested
   subdirectory inherits the project's root instructions), plus a user-global
   file (``~/.deepcode/AGENTS.md`` or ``~/.claude/CLAUDE.md``). Injected verbatim
   into the system prompt as standing guidance the agent should always honor.

2. **Persistent memory** — ``<workspace>/.deepcode/memory/``, which the agent
   reads and writes through the :class:`MemoryTool`. ``MEMORY.md`` is the
   index and is auto-loaded into the system prompt on every session, so
   durable facts (decisions, conventions, gotchas) survive across
   conversations.

Both are assembled once, in :func:`core.agent_setup.build_agent_session`, so
every frontend — TUI, web, headless exec — gets memory identically. The
memory directory lives inside the workspace, so the P1 permission engine
already fences writes to it; the tool additionally refuses any name that
escapes the memory directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters

_MEMORY_SUBDIR = ".deepcode/memory"
_INDEX_FILE = "MEMORY.md"
_PROJECT_FILES = ("AGENTS.md", "DEEPCODE.md", "CLAUDE.md")
# Markers that identify the enclosing project root when the workspace is a
# subdirectory (mirrors the reference agent walking up to the repo root).
_PROJECT_ROOT_MARKERS = (".git",)
# User-level standing instructions that apply across every project (lowest
# precedence). Native first, then Claude Code interop.
_USER_GLOBAL_FILES = ((".deepcode", "AGENTS.md"), (".claude", "CLAUDE.md"))
_MAX_INJECT_CHARS = 8000  # keep the preamble bounded; the tool reads the rest


def memory_dir(workspace: str | Path) -> Path:
    return Path(workspace) / _MEMORY_SUBDIR


def _read_capped(path: Path, cap: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:cap] + "\n…[truncated]" if len(text) > cap else text


def _find_project_root(start: Path) -> Path | None:
    """The nearest ancestor of ``start`` (inclusive) holding a project marker
    (``.git``) — i.e. the enclosing repo root, or ``None`` if there is none."""
    for directory in (start, *start.parents):
        if any((directory / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
            return directory
    return None


def project_instructions(workspace: str | Path) -> str:
    """Project-instruction files from the repo root down to the workspace.

    Mirrors the reference agent's AGENTS.md discovery: find the enclosing repo
    root, then read the first matching instructions file in each directory from
    the root down to the workspace, so a monorepo-root file and a nearer
    subdirectory one both apply (nearest last, highest precedence). When the
    workspace is not inside a repo, only the workspace directory is read.
    """
    workspace = Path(workspace).resolve()
    root = _find_project_root(workspace)
    if root is None or root == workspace:
        search_dirs = [workspace]
    else:
        chain, cursor = [workspace], workspace
        while cursor != root and cursor.parent != cursor:
            cursor = cursor.parent
            chain.append(cursor)
        search_dirs = list(reversed(chain))  # root first → workspace last

    blocks: list[str] = []
    remaining = _MAX_INJECT_CHARS
    for directory in search_dirs:
        for name in _PROJECT_FILES:
            candidate = directory / name
            if candidate.is_file():
                body = _read_capped(candidate, remaining).strip()
                if body:
                    label = name if directory == workspace else f"{directory}/{name}"
                    blocks.append(f"## Project instructions (from {label})\n\n{body}")
                    remaining -= len(body)
                break  # one file per directory: AGENTS.md > DEEPCODE.md > CLAUDE.md
        if remaining <= 0:
            break
    return "\n\n".join(blocks)


def user_global_instructions(home: str | Path | None = None) -> str:
    """User-level standing instructions that apply across every project
    (``~/.deepcode/AGENTS.md`` or ``~/.claude/CLAUDE.md``) — lowest precedence."""
    base = Path(home) if home is not None else Path.home()
    for subdir, name in _USER_GLOBAL_FILES:
        candidate = base / subdir / name
        if candidate.is_file():
            body = _read_capped(candidate, _MAX_INJECT_CHARS).strip()
            if body:
                return f"## User instructions (from ~/{subdir}/{name})\n\n{body}"
    return ""


def memory_index(workspace: str | Path) -> str:
    """Return the persistent MEMORY.md index, if the agent has written one."""
    index = memory_dir(workspace) / _INDEX_FILE
    if index.is_file():
        body = _read_capped(index, _MAX_INJECT_CHARS)
        if body.strip():
            return f"## Memory (from {_MEMORY_SUBDIR}/{_INDEX_FILE})\n\n{body.strip()}"
    return ""


_MEMORY_USAGE = (
    "You have a `memory` tool for persistent notes under "
    f"`{_MEMORY_SUBDIR}/`. When you learn a durable fact — a project "
    "convention, an architectural decision, a gotcha, or a user preference — "
    f"record it so future sessions benefit, and keep `{_INDEX_FILE}` as a "
    "short index of what you know. Read a note before relying on it; it "
    "reflects a past session and may be stale."
)


def system_preamble(workspace: str | Path, home: str | Path | None = None) -> str:
    """The memory addendum to append to the system prompt (may be empty of
    content but always states the memory tool exists).

    Precedence, lowest to highest: user-global instructions, project
    instructions (repo root → workspace), then the persistent memory index.
    """
    parts = [
        user_global_instructions(home),
        project_instructions(workspace),
        memory_index(workspace),
        _MEMORY_USAGE,
    ]
    return "\n\n".join(p for p in parts if p)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "write", "append", "delete"],
                "description": "The memory operation to perform.",
            },
            "name": {
                "type": "string",
                "description": "Memory file name (e.g. MEMORY.md, decisions.md). "
                "Required for all actions except list.",
            },
            "content": {
                "type": "string",
                "description": "Text to write or append (for write/append).",
            },
        },
        "required": ["action"],
    }
)
class MemoryTool(Tool):
    """Read/write persistent memory notes under ``<workspace>/.deepcode/memory``."""

    def __init__(self, workspace: str):
        self._dir = memory_dir(workspace)

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "Persistent notes that survive across sessions, stored under "
            ".deepcode/memory/. actions: list | read | write | append | "
            "delete. Keep MEMORY.md as the index of what you know."
        )

    def _resolve(self, name: str) -> Path | None:
        """Resolve ``name`` inside the memory dir, or None if it escapes."""
        if not name or name != Path(name).name:
            return None  # no subdirs / traversal — a flat notes namespace
        return self._dir / name

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action", "")).lower()
        name = kwargs.get("name") or ""
        content = kwargs.get("content") or ""

        if action == "list":
            if not self._dir.is_dir():
                return "(no memory yet)"
            names = sorted(p.name for p in self._dir.iterdir() if p.is_file())
            return "\n".join(names) if names else "(no memory yet)"

        target = self._resolve(name)
        if target is None:
            return f"Error: invalid memory name: {name!r} (use a plain file name)."

        if action == "read":
            if not target.is_file():
                return f"Error: no such memory: {name}"
            return target.read_text(encoding="utf-8", errors="replace")

        if action in ("write", "append"):
            if not content.strip():
                return "Error: content is required for write/append."
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                if action == "append" and target.is_file():
                    existing = target.read_text(encoding="utf-8", errors="replace")
                    content = existing.rstrip() + "\n" + content
                target.write_text(content, encoding="utf-8")
            except OSError as exc:
                return f"Error: could not write memory {name}: {exc}"
            return f"Saved memory: {name} ({len(content)} chars)."

        if action == "delete":
            if not target.is_file():
                return f"Error: no such memory: {name}"
            try:
                target.unlink()
            except OSError as exc:
                return f"Error: could not delete memory {name}: {exc}"
            return f"Deleted memory: {name}."

        return f"Error: unknown action: {action}"
