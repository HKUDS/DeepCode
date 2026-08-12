"""Deterministic stdio MCP server used by generic runtime integration tests."""

from __future__ import annotations

import asyncio
import os

from mcp import types
from mcp.server.fastmcp import FastMCP

server = FastMCP(
    "deepcode-runtime-fixture",
    instructions="Use read_value for deterministic fixture reads.",
    log_level="ERROR",
)


@server.tool(annotations=types.ToolAnnotations(readOnlyHint=True))
def read_value(name: str = "FIXTURE_VALUE") -> str:
    """Read one environment value from the fixture process."""

    return os.environ.get(name, "<missing>")


@server.tool(
    annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def write_value(value: str) -> str:
    """Return a value while advertising mutating semantics."""

    return f"wrote:{value}"


@server.tool()
async def slow(seconds: float) -> str:
    """Sleep long enough to exercise timeout and cancellation paths."""

    await asyncio.sleep(seconds)
    return "finished"


@server.resource("fixture://status")
def fixture_status() -> str:
    """Expose one deterministic resource for connection probes."""

    return "fixture-ready"


@server.prompt()
def fixture_prompt(topic: str = "MCP") -> str:
    """Expose one deterministic prompt for connection probes."""

    return f"Explain {topic} briefly."


if __name__ == "__main__":
    server.run(transport="stdio")
