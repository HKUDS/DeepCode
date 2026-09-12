"""Native bash tool (P2).

Runs a shell command inside the P1 workspace sandbox (reusing
``core.harness.sandbox.build_exec_command`` — no duplicated sandbox logic).
Large output is capped and spilled to a temp file with an inline preview, so
a chatty command never blows the context. A small declarative preflight
refuses known-interactive scaffolds that would otherwise hang the agent.

Two screens run before the command reaches the shell, and they exist because
the command text is not necessarily the model's own: an intermediary between
us and the provider can rewrite a tool call on its way back. ``screen_all``
(:mod:`core.harness.command_guard`) catches destructive argv, remote scripts
piped into an interpreter, and one-edit package names. The child also gets a
credential-scrubbed environment (:mod:`core.harness.env_sanitize`) so a plain
``env`` no longer copies every provider key into the transcript — and from
there into the next request the model sends, where a relay reads it in
plaintext.

Neither screen is the security boundary. The sandbox is. Both are cheap first
passes that fail closed on shapes we can recognise, and both are waivable on
purpose (``DEEPCODE_ALLOW_REMOTE_SCRIPT``, ``DEEPCODE_BASH_FULL_ENV``,
``DEEPCODE_COMMAND_SCREEN``).
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.agent_runtime.processes import (
    subprocess_group_kwargs,
    terminate_process_tree,
)
from core.agent_runtime.tools.base import Tool, ToolResult, tool_parameters
from core.harness.command_guard import screen_all
from core.harness.env_sanitize import scrubbed_parent_env
from core.harness.sandbox import build_exec_command

_MAX_OUTPUT_CHARS = 30_000
_DEFAULT_TIMEOUT = 120

# Declarative preflight: (needle in command, required non-interactive flag,
# hint). Interactive scaffolds hang forever waiting on stdin; refuse fast with
# a fix instead of timing out. Add a case = add a row (no scattered ifs).
_INTERACTIVE_SCAFFOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("create-next-app", ("--yes", "-y"), "pass --yes"),
    ("npm init", ("--yes", "-y"), "use `npm init -y`"),
    ("npm create", ("--yes", "-y"), "pass --yes"),
    ("yarn create", ("--yes", "-y"), "pass --yes"),
    ("create-react-app", ("--template",), "specify --template to avoid prompts"),
    ("vue create", ("--default", "-d"), "use `vue create -d`"),
)


def _preflight(command: str) -> str | None:
    lowered = command.lower()
    for needle, flags, hint in _INTERACTIVE_SCAFFOLDS:
        if needle in lowered and not any(f in lowered for f in flags):
            return (
                f"Refusing to run an interactive scaffold ('{needle}') that would "
                f"hang waiting for input. Re-run non-interactively: {hint}."
            )
    return None


# Manifest files worth reading for declared dependency names. Parsing is
# deliberately shallow: we only need names to compare against, and a missed
# name only costs us a weaker typosquat check — it never blocks anything.
_REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PYPROJECT_DEP = re.compile(r"[\"']([A-Za-z0-9][A-Za-z0-9._-]*)")
_MAX_MANIFEST_BYTES = 200_000


def _declared_packages(workspace: str) -> frozenset[str]:
    """Dependency names declared by the workspace, best effort.

    Used to spot a package that is one edit away from something the project
    already depends on. Reading the real manifest beats any built-in list: the
    confusable that matters is the one *this* project would plausibly install.
    """

    names: set[str] = set()
    root = Path(workspace)

    for candidate in sorted(root.glob("requirements*.txt"))[:5]:
        text = _read_manifest(candidate)
        for line in text.splitlines():
            line = line.split("#", 1)[0]
            match = _REQUIREMENT_LINE.match(line)
            if match:
                names.add(match.group(1))

    text = _read_manifest(root / "package.json")
    if text:
        try:
            import json

            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("dependencies", "devDependencies"):
                block = payload.get(key)
                if isinstance(block, dict):
                    names.update(str(name) for name in block)

    text = _read_manifest(root / "pyproject.toml")
    if text:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("dependencies", '"', "'", "[")) or "=" in stripped:
                match = _PYPROJECT_DEP.search(line)
                if match:
                    names.add(match.group(1))

    return frozenset(name for name in names if name)


def _read_manifest(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run."},
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {_DEFAULT_TIMEOUT}).",
            },
        },
        "required": ["command"],
    }
)
class BashTool(Tool):
    """Run a bash command in the sandboxed workspace and return its output."""

    def __init__(self, workspace: str, *, sandbox_enabled: bool | None = None):
        self._workspace = str(workspace)
        self._sandbox_enabled = sandbox_enabled
        self._declared_packages: frozenset[str] | None = None

    def _known_packages(self) -> frozenset[str]:
        """Declared dependency names, read once per tool instance."""

        if self._declared_packages is None:
            self._declared_packages = _declared_packages(self._workspace)
        return self._declared_packages

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        if self._sandbox_enabled is False:
            return (
                "Run a bash command with the command sandbox disabled by the "
                "current Full Access profile. Prefer non-interactive flags; "
                "large output is truncated with the full output saved to a file."
            )
        return (
            "Run a bash command in the workspace (sandboxed: writes are fenced "
            "to the workspace). Prefer non-interactive flags; large output is "
            "truncated with the full output saved to a file."
        )

    async def execute(self, **kwargs: Any) -> Any:
        command = kwargs.get("command", "")
        timeout = int(kwargs.get("timeout") or _DEFAULT_TIMEOUT)
        if not command.strip():
            return "Error: empty command."

        refusal = _preflight(command)
        if refusal:
            return f"Error: {refusal}"

        # Fail closed on shapes we can recognise: destructive argv, a remote
        # script piped into an interpreter, a package one edit from a declared
        # dependency, or an install from a non-canonical index. The rewritten
        # tool call an intermediary would deliver is schema-valid, so the
        # arguments are the only place it can show.
        screened = screen_all(command, known_packages=self._known_packages())
        if screened:
            return (
                f"Error: command blocked by policy screen ({screened}). "
                "If this is intended, re-run with the matching DEEPCODE_* waiver."
            )

        wrapped = build_exec_command(
            command=command,
            workspace=self._workspace,
            enabled=self._sandbox_enabled,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *wrapped.argv,
                cwd=self._workspace,
                # Credential-shaped variables are dropped so a plain `env` (or
                # any command that echoes one) cannot copy provider keys into
                # the transcript and from there into the next outbound request.
                env=scrubbed_parent_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **subprocess_group_kwargs(),
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                await terminate_process_tree(proc)
                return f"Error: command timed out after {timeout}s: {command}"
            except asyncio.CancelledError:
                await terminate_process_tree(proc)
                raise
        except OSError as exc:
            return f"Error: could not run command: {exc}"
        finally:
            wrapped.cleanup()

        text = out.decode("utf-8", errors="replace")
        rc = proc.returncode
        assert rc is not None
        header = f"[exit {rc}]" if rc else ""
        if len(text) <= _MAX_OUTPUT_CHARS:
            content = f"{header}\n{text}".strip() if header else text
            return ToolResult(
                content,
                is_error=rc != 0,
                metadata={"exit_code": rc},
            )

        # Spill full output to a file; return a bounded preview + the path.
        fd, path = tempfile.mkstemp(prefix="deepcode-bash-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        preview = text[-_MAX_OUTPUT_CHARS:]
        content = (
            f"{header}\n...output truncated ({len(text)} chars). "
            f"Full output saved to: {path}\n\n{preview}"
        ).strip()
        return ToolResult(
            content,
            is_error=rc != 0,
            metadata={"exit_code": rc},
        )
