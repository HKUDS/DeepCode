"""Lazy MCP server activation (deferLoading) tests.

``McpServerDefinition.defer_loading`` skips a server at ``ensure_started``;
``McpSessionRuntime.activate_server`` brings it up on demand and registers its
tools. This mirrors the CLI-side lazy-connect design (CodeWhale-derived).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.agent_runtime.tools.registry import ToolRegistry
from core.mcp.models import (
    McpRuntimePlan,
    McpServerDefinition,
    McpServerSource,
    ResolvedMcpServer,
)
from core.mcp.runtime import McpSessionRuntime

FAKE_SERVER_SRC = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            json.dump({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1.0"}}}, sys.stdout)
            print(flush=True)
        elif method == "tools/list":
            json.dump({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "echo", "description": "echo back",
                 "inputSchema": {"type": "object", "properties": {}}}]}},
                sys.stdout)
            print(flush=True)
    """
)


class LazyActivationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="deepcode-mcp-lazy-")
        self.fake_server = Path(self.tmp) / "fake_mcp_server.py"
        self.fake_server.write_text(FAKE_SERVER_SRC, encoding="utf-8")

    def _server(self, server_id: str, defer_loading: bool) -> ResolvedMcpServer:
        return ResolvedMcpServer(
            server_id=server_id,
            name=server_id,
            source=McpServerSource.USER,
            definition=McpServerDefinition(
                type="stdio",
                command=sys.executable,
                args=(str(self.fake_server),),
                startup_timeout_seconds=10.0,
                defer_loading=defer_loading,
            ),
            config_dir=Path(self.tmp),
            workspace=Path(self.tmp),
        )

    def _plan(self, *servers: ResolvedMcpServer) -> McpRuntimePlan:
        return McpRuntimePlan(
            workspace=Path(self.tmp),
            servers=tuple(servers),
            revision="test",
        )

    async def test_deferred_server_not_started_at_ensure(self) -> None:
        registry = ToolRegistry()
        runtime = McpSessionRuntime(
            self._plan(self._server("eager1", False), self._server("lazy1", True)),
            registry,
        )
        try:
            await runtime.ensure_started()
            statuses = {s.server_id: s for s in runtime.statuses}
            self.assertEqual(statuses["eager1"].state, "ready")
            self.assertEqual(statuses["lazy1"].state, "deferred")
            self.assertNotIn("lazy1", runtime.available_server_ids)
            # Only the eager server's remote tool is registered, plus the
            # activation bridge that makes deferred servers reachable.
            self.assertIn("eager1", runtime.skill_capabilities)
            self.assertNotIn("lazy1", runtime.skill_capabilities)
            self.assertIn("mcp__deepcode_runtime__activate_server", registry.tool_names)
        finally:
            await runtime.aclose()
        self.assertNotIn("mcp__deepcode_runtime__activate_server", registry.tool_names)

    async def test_activate_server_registers_tools(self) -> None:
        registry = ToolRegistry()
        runtime = McpSessionRuntime(
            self._plan(self._server("eager1", False), self._server("lazy1", True)),
            registry,
        )
        try:
            await runtime.ensure_started()
            result = await registry.execute(
                "mcp__deepcode_runtime__activate_server", {"server_id": "lazy1"}
            )
            self.assertIn("activated", str(result))
            statuses = {s.server_id: s for s in runtime.statuses}
            self.assertEqual(statuses["lazy1"].state, "ready")
            self.assertIn("lazy1", runtime.available_server_ids)
            self.assertIn("lazy1", runtime.skill_capabilities)
            self.assertGreater(len(runtime.skill_capabilities["lazy1"]), 0)
            self.assertIn("mcp__lazy1__echo", registry.tool_names)
        finally:
            await runtime.aclose()

    async def test_activate_is_idempotent(self) -> None:
        runtime = McpSessionRuntime(
            self._plan(self._server("lazy1", True)),
            ToolRegistry(),
        )
        await runtime.ensure_started()
        try:
            self.assertTrue(await runtime.activate_server("lazy1"))
            tools_after_first = set(runtime.skill_capabilities.get("lazy1", ()))
            self.assertTrue(await runtime.activate_server("lazy1"))
            tools_after_second = set(runtime.skill_capabilities.get("lazy1", ()))
            self.assertEqual(tools_after_first, tools_after_second)
        finally:
            await runtime.aclose()

    async def test_activate_unknown_server_returns_false(self) -> None:
        runtime = McpSessionRuntime(
            self._plan(self._server("lazy1", True)),
            ToolRegistry(),
        )
        await runtime.ensure_started()
        try:
            self.assertFalse(await runtime.activate_server("no-such-server"))
        finally:
            await runtime.aclose()

    async def test_activate_after_close_returns_false(self) -> None:
        runtime = McpSessionRuntime(
            self._plan(self._server("lazy1", True)),
            ToolRegistry(),
        )
        await runtime.aclose()
        self.assertFalse(await runtime.activate_server("lazy1"))

    async def test_activation_cancellation_propagates_and_restores_state(self) -> None:
        runtime = McpSessionRuntime(
            self._plan(self._server("lazy1", True)),
            ToolRegistry(),
        )
        await runtime.ensure_started()

        async def cancel_start(_connection):
            raise asyncio.CancelledError

        try:
            with (
                patch("core.mcp.runtime.McpConnection.start", cancel_start),
                self.assertRaises(asyncio.CancelledError),
            ):
                await runtime.activate_server("lazy1")
            statuses = {s.server_id: s for s in runtime.statuses}
            self.assertEqual(statuses["lazy1"].state, "deferred")
        finally:
            await runtime.aclose()

    async def test_activate_failure_marks_failed(self) -> None:
        broken = ResolvedMcpServer(
            server_id="broken1",
            name="broken1",
            source=McpServerSource.USER,
            definition=McpServerDefinition(
                type="stdio",
                command=sys.executable,
                args=("C:/definitely_missing_mcp_server_xyz.py",),
                startup_timeout_seconds=5.0,
                defer_loading=True,
            ),
            config_dir=Path(self.tmp),
            workspace=Path(self.tmp),
        )
        runtime = McpSessionRuntime(self._plan(broken), ToolRegistry())
        await runtime.ensure_started()
        try:
            self.assertFalse(await runtime.activate_server("broken1"))
            statuses = {s.server_id: s for s in runtime.statuses}
            self.assertEqual(statuses["broken1"].state, "failed")
            self.assertIsNotNone(statuses["broken1"].error)
        finally:
            await runtime.aclose()


def test_required_server_cannot_be_deferred() -> None:
    with pytest.raises(ValidationError, match="required MCP servers cannot defer"):
        McpServerDefinition(
            type="stdio",
            command=sys.executable,
            required=True,
            defer_loading=True,
        )


if __name__ == "__main__":
    unittest.main()
