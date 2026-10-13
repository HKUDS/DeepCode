"""Native grep + glob tools (P2).

grep uses ripgrep when available (fast, respects .gitignore) and falls back to
a pure-Python regex walk otherwise — same output either way. glob uses
pathlib, so no external dependency. Both are read-only and scoped to the
workspace. Unless protection is off (Full access), neither returns anything
from a protected credential file found inside the searched directory: the
permission engine only sees the directory the model named.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from core.agent_runtime.processes import run_captured
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.permissions import is_sensitive_path

_MAX_MATCHES = 200
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search (relative to workspace; default workspace root).",
            },
            "include": {
                "type": "string",
                "description": "Optional glob to limit files, e.g. '*.py'.",
            },
        },
        "required": ["pattern"],
    }
)
class GrepTool(Tool):
    """Search file contents by regex, returning ``path:line: text`` matches."""

    def __init__(self, workspace: str, *, protect_sensitive_paths: bool = True):
        # Resolved like the search roots below, so results are relative to it
        # even when the workspace is reached through a link (/tmp on macOS).
        self._workspace = str(Path(workspace).resolve())
        self._protect = protect_sensitive_paths

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents by regular expression. Returns matching "
            "`path:line: text` lines. Optionally restrict files with `include` "
            "(e.g. '*.py')."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        pattern = kwargs.get("pattern", "")
        if not pattern:
            return "Error: pattern is required."
        rel = kwargs.get("path") or "."
        include = kwargs.get("include")
        root = (Path(self._workspace) / rel).resolve()
        if not root.exists():
            return f"Error: path not found: {rel}"

        # Either search can take seconds (rg is allowed 30). Neither runs on
        # the event loop, which the service shares with every other session.
        if shutil.which("rg"):
            matches = await self._ripgrep(pattern, root, include)
        else:
            matches = await asyncio.to_thread(self._python_grep, pattern, root, include)
        if matches is None:
            return f"Error: invalid regular expression: {pattern}"
        if not matches:
            return "No matches."
        out = matches[:_MAX_MATCHES]
        suffix = (
            f"\n... ({len(matches) - _MAX_MATCHES} more matches; refine the pattern)"
            if len(matches) > _MAX_MATCHES
            else ""
        )
        return "\n".join(out) + suffix

    async def _ripgrep(
        self, pattern: str, root: Path, include: str | None
    ) -> list[str] | None:
        argv = ["rg", "--line-number", "--no-heading", "--color", "never"]
        # Every match starts with its file's path, ended by NUL, so the path
        # can be checked against the protected-path guard below.
        argv += ["--with-filename", "--null"]
        # Both come from the model: attached with "=" they stay values, so a
        # pattern like "--pre=<program>" can't make rg run a program.
        if include:
            argv.append(f"--glob={include}")
        argv += [f"--regexp={pattern}", "--", str(root)]
        try:
            # An interrupted turn ends rg instead of leaving it searching.
            result = await run_captured(argv, timeout=30)
        except OSError:
            result = None
        if result is None:
            return await asyncio.to_thread(self._python_grep, pattern, root, include)
        returncode, stdout, stderr = result
        if returncode not in (0, 1):  # 1 = no matches; >1 = error
            if b"regex parse error" in stderr.lower():
                return None
            return await asyncio.to_thread(self._python_grep, pattern, root, include)
        matches: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            path, sep, rest = line.partition("\0")
            if not sep or (self._protect and is_sensitive_path(path)):
                continue
            # make paths workspace-relative for readability
            matches.append(f"{self._relativize(path)}:{rest}")
        return matches

    def _python_grep(
        self, pattern: str, root: Path, include: str | None
    ) -> list[str] | None:
        try:
            regex = re.compile(pattern)
        except re.error:
            return None
        results: list[str] = []
        targets = [root] if root.is_file() else self._walk(root, include)
        for file in targets:
            if self._protect and is_sensitive_path(str(file)):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = os.path.relpath(file, self._workspace)
                    results.append(f"{rel}:{i}: {line.strip()[:300]}")
                    if len(results) > _MAX_MATCHES * 2:
                        return results
        return results

    def _walk(self, root: Path, include: str | None):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if include and not Path(name).match(include):
                    continue
                yield Path(dirpath) / name

    def _relativize(self, path: str) -> str:
        if os.path.isabs(path):
            try:
                return os.path.relpath(path, self._workspace)
            except ValueError:
                return path
        return path


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/*.ts'.",
            },
            "path": {
                "type": "string",
                "description": "Base directory (relative to workspace; default root).",
            },
        },
        "required": ["pattern"],
    }
)
class GlobTool(Tool):
    """List files matching a glob pattern, workspace-relative."""

    def __init__(self, workspace: str, *, protect_sensitive_paths: bool = True):
        # Resolved like the search roots below, so results are relative to it
        # even when the workspace is reached through a link (/tmp on macOS).
        self._workspace = str(Path(workspace).resolve())
        self._protect = protect_sensitive_paths

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "List files matching a glob pattern (e.g. '**/*.py'), workspace-relative."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        pattern = kwargs.get("pattern", "")
        if not pattern:
            return "Error: pattern is required."
        # A deep tree takes a while to walk: not on the event loop either.
        return await asyncio.to_thread(self._glob, pattern, kwargs.get("path"))

    def _glob(self, pattern: str, path: str | None) -> str:
        base = (Path(self._workspace) / (path or ".")).resolve()
        if not base.exists():
            return f"Error: path not found: {path}"
        matches = []
        for p in sorted(base.glob(pattern)):
            # Prune relative to the base only. ``p.parts`` also carries the
            # workspace's own ancestors, so a workspace rooted under a
            # directory named e.g. "build" or "venv" skipped every file.
            rel = os.path.relpath(p, base)
            if any(part in _SKIP_DIRS for part in Path(rel).parts):
                continue
            if self._protect and is_sensitive_path(str(p)):
                continue
            if p.is_file():
                matches.append(os.path.relpath(p, self._workspace))
        if not matches:
            return "No files matched."
        out = matches[:_MAX_MATCHES]
        suffix = (
            f"\n... ({len(matches) - _MAX_MATCHES} more)"
            if len(matches) > _MAX_MATCHES
            else ""
        )
        return "\n".join(out) + suffix
