"""``code`` tool — code mode (C5b), the parent side.

Instead of the model calling tools one at a time, it writes a small Python
program that calls the available tools as functions; this tool runs that program
in a sandboxed subprocess and dispatches every tool call back to the parent so
loops, conditionals, and batching happen in a single turn.

Ported in spirit from the reference agent's ``code_mode`` (which embeds V8 and
runs the model's JavaScript): the runtime that confines the code is DeepCode's
platform sandbox instead of V8, and the code language is Python. Each tool call
the code makes is executed by ``execute_tool`` in the parent, so it passes the
same permission checks and hooks as a normal tool call — the sandboxed child
never touches the real tools directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.harness.sandbox import build_exec_command

_RUNNER = str(Path(__file__).with_name("_runner.py"))
_DEFAULT_TIMEOUT_S = 300.0
_MAX_RESULT_CHARS = 20_000
# asyncio's StreamReader defaults to a 64KB line limit; a single RPC line can be
# much larger (a write with big content, or a big captured output), so raise it.
_STREAM_LIMIT = 16 * 1024 * 1024


@dataclass(slots=True)
class ToolAPISpec:
    """One tool exposed to code mode as a callable function."""

    name: str
    params: list[str]  # positional parameter order
    signature: str  # e.g. "read(file_path)"
    doc: str


def api_from_definitions(
    definitions: list[dict], exposed: frozenset[str]
) -> list[ToolAPISpec]:
    """Build code-mode API specs from OpenAI-style tool definitions.

    Only tools whose name is in ``exposed`` are surfaced. Required parameters
    come first (schema order) so positional calls like ``read("x.py")`` map
    correctly; optional ones follow as ``name=None`` in the shown signature.
    """
    specs: list[ToolAPISpec] = []
    for definition in definitions:
        fn = definition.get("function", definition)
        name = fn.get("name")
        if name not in exposed:
            continue
        schema = fn.get("parameters") or {}
        props = list((schema.get("properties") or {}).keys())
        required = [p for p in (schema.get("required") or []) if p in props]
        optional = [p for p in props if p not in required]
        params = required + optional
        shown = ", ".join(list(required) + [f"{p}=None" for p in optional])
        doc = (fn.get("description") or "").split(". ", 1)[0].strip()[:90]
        specs.append(ToolAPISpec(name, params, f"{name}({shown})", doc))
    return specs


# execute_tool(tool_name, arguments) -> result string (already permission/hook
# governed by the parent). Raising is turned into an error the code can catch.
GovernedExecute = Callable[[str, dict[str, Any]], Awaitable[str]]


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "A Python program that calls the available tools as "
                "functions. print() anything you want to see, or assign a top-level "
                "`result` variable to return structured data.",
            }
        },
        "required": ["code"],
    }
)
class CodeModeTool(Tool):
    """Run a Python program that orchestrates the other tools (code mode)."""

    def __init__(
        self,
        workspace: str,
        execute_tool: GovernedExecute,
        api: list[ToolAPISpec],
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._workspace = os.path.abspath(workspace)
        self._execute_tool = execute_tool
        self._api = list(api)
        self._api_by_name = {spec.name: spec for spec in api}
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "code"

    @property
    def description(self) -> str:
        lines = [spec.signature + (f"  # {spec.doc}" if spec.doc else "") for spec in self._api]
        api_block = "\n".join(lines) if lines else "(no tools exposed)"
        return (
            "Code mode: write a Python program that calls the tools below as "
            "functions to do many operations in one step — loops, conditionals, "
            "and batching — instead of one tool call at a time. Prefer this when a "
            "task needs several dependent or repeated tool calls. The program runs "
            "in the sandboxed workspace; each function call runs the real tool with "
            "the usual permission checks. print() what you want to see, or set a "
            "top-level `result` variable to return structured data. Available tools:\n"
            f"{api_block}"
        )

    async def execute(self, **kwargs: Any) -> Any:
        code = kwargs.get("code")
        if not isinstance(code, str) or not code.strip():
            return "Error: 'code' is required — a Python program to run."

        init = {
            "code": code,
            "tools": [{"name": s.name, "params": s.params} for s in self._api],
        }

        wrapped = build_exec_command(
            argv=[sys.executable, _RUNNER],
            workspace=self._workspace,
            allow_network=False,
        )
        try:
            return await self._run(wrapped.argv, init)
        finally:
            wrapped.cleanup()

    async def _run(self, argv: list[str], init: dict) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace,
                env={**os.environ},
                limit=_STREAM_LIMIT,
            )
        except OSError as exc:
            return f"Error: could not start code-mode runtime: {exc}"

        proc.stdin.write((json.dumps(init) + "\n").encode())
        await proc.stdin.drain()

        try:
            done = await asyncio.wait_for(self._rpc_loop(proc), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await self._reap(proc)
            return f"Error: code timed out after {self._timeout_s:g}s (a tool call or loop hung)."

        stderr = (await self._reap(proc)).strip()
        if done is None:
            detail = stderr or "the code-mode runtime exited before finishing"
            return f"Error: code mode failed: {detail}"
        return self._format(done)

    async def _rpc_loop(self, proc: asyncio.subprocess.Process) -> dict | None:
        """Dispatch each tool call the child makes; return its final message."""
        while True:
            line = await proc.stdout.readline()
            if not line:
                return None  # child closed without a done message
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # ignore any stray non-protocol line
            if "call" in msg:
                value, error = await self._dispatch(msg["call"], msg.get("args") or {})
                reply = {"ok": error is None, "value": value, "error": error}
                proc.stdin.write((json.dumps(reply) + "\n").encode())
                await proc.stdin.drain()
            elif msg.get("done"):
                return msg

    async def _dispatch(self, tool_name: str, arguments: dict) -> tuple[str | None, str | None]:
        if tool_name not in self._api_by_name:
            return None, f"unknown tool {tool_name!r} (not exposed to code mode)"
        try:
            result = await self._execute_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 - surface tool failures to the code
            return None, f"{type(exc).__name__}: {exc}"
        return ("" if result is None else str(result)), None

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process) -> str:
        try:
            _out, err = await proc.communicate()
        except Exception:  # noqa: BLE001 - best-effort teardown
            return ""
        return err.decode(errors="replace") if err else ""

    @staticmethod
    def _format(done: dict) -> str:
        parts: list[str] = []
        output = (done.get("output") or "").strip()
        if output:
            parts.append(output)
        result = done.get("result")
        if result is not None:
            parts.append(f"result = {result}")
        error = done.get("error")
        if error:
            parts.append(f"The code raised an error:\n{error.strip()}")
        text = "\n\n".join(parts) if parts else "(code produced no output)"
        if len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS] + "\n… (truncated)"
        return text
