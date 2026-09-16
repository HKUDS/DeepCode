"""``deepcode mcp`` — expose DeepCode as an MCP server (C5a).

The mirror of DeepCode's MCP *client*: instead of DeepCode calling other MCP
servers for tools, this lets any MCP client (another agent, an IDE, a second
DeepCode) drive DeepCode itself as a coding sub-agent over stdio.

The stdio server submits durable Turns to the shared local service. The
``deepcode`` tool starts a Session; ``deepcode-reply`` resumes its canonical id.
Workspace trust and tool approval remain enforced by the service. Closing MCP
stdio detaches the client; it does not close the Agent or erase Session history.
Logs stay on stderr so stdout remains the MCP protocol channel.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

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
        "call) with a follow-up prompt. The shared service keeps its full history across MCP connections."
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


def _reply(
    text: str, structured: dict[str, Any]
) -> tuple[list[types.TextContent], dict[str, Any]]:
    return [types.TextContent(type="text", text=text)], structured


async def _run_task(prompt: str, *, workspace=None, model=None, session_id=None):
    from cli.thread_client import HeadlessTurnOptions
    from cli.service_turn import run_service_turn_async
    from core.application.errors import ApplicationError

    summary = ""

    def on_event(event):
        nonlocal summary
        if event.msg.type == "agent_message" and event.msg.phase.value == "final_answer":
            summary = event.msg.text
        elif event.msg.type == "task_complete":
            summary = event.msg.final_text or summary

    try:
        result = await run_service_turn_async(
            HeadlessTurnOptions(
                prompt=prompt, workspace=workspace, model=model, resume_id=session_id
            ),
            on_event=on_event,
        )
    except (ApplicationError, OSError, ValueError) as exc:
        return _reply(f"Error: {exc}", {"error": getattr(exc, "code", "task failed")})
    return _reply(
        summary.strip() or "(the agent produced no summary)",
        {
            "session_id": result.session_id,
            "stop_reason": result.turn.stop_reason or result.turn.status.value,
        },
    )


async def _handle_deepcode(arguments: dict[str, Any]):
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return _reply("Error: 'prompt' is required.", {"error": "missing prompt"})
    workspace = os.path.abspath(str(arguments.get("workspace") or os.getcwd()))
    return await _run_task(
        prompt, workspace=workspace, model=arguments.get("model") or None
    )


async def _handle_reply(arguments: dict[str, Any]):
    session_id = str(arguments.get("session_id") or "").strip()
    if not session_id:
        return _reply("Error: 'session_id' is required.", {"error": "missing session"})
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return _reply("Error: 'prompt' is required.", {"error": "missing prompt"})
    return await _run_task(prompt, session_id=session_id)


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
