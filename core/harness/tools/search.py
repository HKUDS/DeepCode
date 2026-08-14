"""Native grep + glob tools (P2).

grep uses ripgrep when available (fast, respects .gitignore) and falls back to
a pure-Python regex walk otherwise — same output either way. glob uses
pathlib, so no external dependency. Both are read-only and scoped to the
workspace.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters

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

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

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

        if shutil.which("rg"):
            matches = self._ripgrep(pattern, root, include)
        else:
            matches = self._python_grep(pattern, root, include)
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

    def _ripgrep(
        self, pattern: str, root: Path, include: str | None
    ) -> list[str] | None:
        argv = ["rg", "--line-number", "--no-heading", "--color", "never"]
        if include:
            argv += ["--glob", include]
        argv += [pattern, str(root)]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return self._python_grep(pattern, root, include)
        if proc.returncode not in (0, 1):  # 1 = no matches; >1 = error
            if "regex parse error" in proc.stderr.lower():
                return None
            return self._python_grep(pattern, root, include)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        # make paths workspace-relative for readability
        return [self._relativize(ln) for ln in lines]

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

    def _relativize(self, line: str) -> str:
        # rg output is "abspath:line:text" — shorten the path to the workspace.
        parts = line.split(":", 1)
        if parts and os.path.isabs(parts[0]):
            try:
                rel = os.path.relpath(parts[0], self._workspace)
                return rel + ":" + parts[1] if len(parts) > 1 else rel
            except ValueError:
                return line
        return line


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

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

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
        base = (Path(self._workspace) / (kwargs.get("path") or ".")).resolve()
        if not base.exists():
            return f"Error: path not found: {kwargs.get('path')}"
        matches = []
        for p in sorted(base.glob(pattern)):
            if any(part in _SKIP_DIRS for part in p.parts):
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
