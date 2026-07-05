"""Native bash tool (P2).

Runs a shell command inside the P1 workspace sandbox (reusing
``core.harness.sandbox.build_exec_command`` — no duplicated sandbox logic).
Large output is capped and spilled to a temp file with an inline preview, so
a chatty command never blows the context. A small declarative preflight
refuses known-interactive scaffolds that would otherwise hang the agent.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
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

    def __init__(self, workspace: str):
        self._workspace = str(workspace)

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
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

        wrapped = build_exec_command(command=command, workspace=self._workspace)
        try:
            proc = await asyncio.create_subprocess_exec(
                *wrapped.argv,
                cwd=self._workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: command timed out after {timeout}s: {command}"
        except OSError as exc:
            return f"Error: could not run command: {exc}"
        finally:
            wrapped.cleanup()

        text = out.decode("utf-8", errors="replace")
        rc = proc.returncode
        header = f"[exit {rc}]" if rc else ""
        if len(text) <= _MAX_OUTPUT_CHARS:
            return f"{header}\n{text}".strip() if header else text

        # Spill full output to a file; return a bounded preview + the path.
        fd, path = tempfile.mkstemp(prefix="deepcode-bash-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        preview = text[-_MAX_OUTPUT_CHARS:]
        return (
            f"{header}\n...output truncated ({len(text)} chars). "
            f"Full output saved to: {path}\n\n{preview}"
        ).strip()
