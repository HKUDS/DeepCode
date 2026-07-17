"""``deepcode mcp`` — expose DeepCode as an MCP server (C5a).

The mirror of DeepCode's MCP *client*: instead of DeepCode calling other MCP
servers for tools, this lets any MCP client (another agent, an IDE, a second
DeepCode) drive DeepCode itself as a coding sub-agent over stdio.

Ported from the reference agent's ``mcp-server/`` (``codex_tool_config`` +
``codex_tool_runner`` + ``message_processor``), adapted to DeepCode's ``mcp``
SDK and :func:`core.agent_setup.build_agent_session`. Two tools are exposed:

- ``deepcode``        — run a coding task on a prompt (starts a session)
- ``deepcode-reply``  — continue a prior session by id (multi-turn)

Every call runs through ``build_agent_session``, so it inherits the full agent
— native tools, hooks, summarization compaction, sandbox, spawn_agent
delegation, skills, and memory. The transport is stdio; the JSON-RPC channel is
stdout, so all logging must stay on stderr (configured below).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# Sessions kept alive for ``deepcode-reply`` follow-ups, keyed by the id we
# return from a ``deepcode`` call (mirrors the reference's thread_id map).
_SESSIONS: dict[str, Any] = {}

_DEEPCODE_TOOL = types.Tool(
    name="deepcode",
    title="DeepCode",
    description=(
        "Run a DeepCode coding session on a prompt. The agent navigates, edits, "
        "and runs code in the given workspace and returns a summary of what it "
        "did. Returns a session_id you can pass to deepcode-reply to continue."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The coding task to perform, in natural language.",
            },
            "workspace": {
                "type": "string",
                "description": "Working directory the agent operates in "
                "(absolute, or relative to the server's cwd). Defaults to the "
                "server's current directory.",
            },
            "model": {
                "type": "string",
                "description": "Optional model id override for this session.",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
)

_REPLY_TOOL = types.Tool(
    name="deepcode-reply",
    title="DeepCode reply",
    description=(
        "Continue an existing DeepCode session (started by a prior deepcode "
        "call) with a follow-up prompt. The session keeps its full history."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session_id returned by a prior deepcode call.",
            },
            "prompt": {
                "type": "string",
                "description": "The next prompt to continue the session.",
            },
        },
        "required": ["session_id", "prompt"],
        "additionalProperties": False,
    },
)


async def _run_turn(session: Any, prompt: str) -> tuple[str, str]:
    """Run one turn on ``session`` and return (final_text, stop_reason)."""
    from core.events import UserInput

    final, stop_reason = "", "completed"
    async for event in session.run_stream(UserInput(text=prompt)):
        if event.msg.type == "task_complete":
            final = event.msg.final_text or ""
            stop_reason = event.msg.stop_reason or "completed"
    return (final.strip() or "(the agent produced no summary)"), stop_reason


def _reply(
    text: str, structured: dict[str, Any]
) -> tuple[list[types.TextContent], dict[str, Any]]:
    return [types.TextContent(type="text", text=text)], structured


async def _handle_deepcode(arguments: dict[str, Any]):
    from core.agent_setup import build_agent_session

    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return _reply("Error: 'prompt' is required.", {"error": "missing prompt"})
    workspace = os.path.abspath(str(arguments.get("workspace") or os.getcwd()))
    model = arguments.get("model") or None
    session, _model, _engine = build_agent_session(workspace=workspace, model=model)
    session_id = uuid.uuid4().hex
    _SESSIONS[session_id] = session
    final, stop_reason = await _run_turn(session, prompt)
    return _reply(final, {"session_id": session_id, "stop_reason": stop_reason})


async def _handle_reply(arguments: dict[str, Any]):
    session_id = str(arguments.get("session_id") or "")
    session = _SESSIONS.get(session_id)
    if session is None:
        return _reply(
            f"Error: no such session {session_id!r}. Start one with the deepcode tool first.",
            {"error": "unknown session"},
        )
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return _reply("Error: 'prompt' is required.", {"error": "missing prompt"})
    final, stop_reason = await _run_turn(session, prompt)
    return _reply(final, {"session_id": session_id, "stop_reason": stop_reason})


def build_server() -> Server:
    """Assemble the ``deepcode`` MCP server (list_tools + call_tool handlers)."""
    server: Server = Server("deepcode")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [_DEEPCODE_TOOL, _REPLY_TOOL]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]):
        if name == "deepcode":
            return await _handle_deepcode(arguments)
        if name == "deepcode-reply":
            return await _handle_reply(arguments)
        return _reply(f"Error: unknown tool {name!r}.", {"error": "unknown tool"})

    return server


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main(argv: list[str] | None = None) -> int:
    # stdout is the JSON-RPC channel — keep every log line on stderr.
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("DEEPCODE_LOG_LEVEL", "WARNING"))
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
