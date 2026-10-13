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
    McpStartupError,
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

# The same server, except that its first launch never answers the handshake:
# one slow enough to time out once that then starts normally.
HANGS_ONCE_SRC = (
    textwrap.dedent(
        """
        import os, sys
        if not os.path.exists(sys.argv[1]):
            open(sys.argv[1], "a").close()
            sys.stdin.read()
            sys.exit()
        """
    )
    + FAKE_SERVER_SRC
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

    def _hangs_once_server(
        self,
        server_id: str,
        *,
        required: bool = False,
        startup_timeout_seconds: float = 0.5,
    ) -> ResolvedMcpServer:
        script = Path(self.tmp) / f"{server_id}_server.py"
        script.write_text(HANGS_ONCE_SRC, encoding="utf-8")
        return ResolvedMcpServer(
            server_id=server_id,
            name=server_id,
            source=McpServerSource.USER,
            definition=McpServerDefinition(
                type="stdio",
                command=sys.executable,
                args=(str(script), str(Path(self.tmp) / f"{server_id}.launched")),
                startup_timeout_seconds=startup_timeout_seconds,
                required=required,
            ),
            config_dir=Path(self.tmp),
            workspace=Path(self.tmp),
        )

    async def test_startup_timeout_defers_the_server_in_later_sessions(self) -> None:
        plan = self._plan(self._hangs_once_server("slow1"))
        first = McpSessionRuntime(plan, ToolRegistry())
        try:
            await first.ensure_started()
            status = first.statuses[0]
            self.assertEqual(status.state, "failed")
            self.assertIn("timed out", status.error or "")
            self.assertIn("new sessions", status.error or "")
        finally:
            await first.aclose()

        # The server would start now, so a later session that started it
        # eagerly would see it ready. It must be left to first use instead.
        registry = ToolRegistry()
        later = McpSessionRuntime(plan, registry)
        try:
            await later.ensure_started()
            status = later.statuses[0]
            self.assertEqual(status.state, "deferred")
            self.assertIn("timed out", status.error or "")
            self.assertNotIn("slow1", later.available_server_ids)
            activation = registry.get("mcp__deepcode_runtime__activate_server")
            self.assertIsNotNone(activation)
            self.assertIn("timed out", activation.description)
            result = await registry.execute(
                "mcp__deepcode_runtime__activate_server", {"server_id": "slow1"}
            )
            self.assertIn("activated", str(result))
            self.assertIn("mcp__slow1__echo", registry.tool_names)
        finally:
            await later.aclose()

        # An edited definition, say a longer timeout, is waited for again.
        edited = self._hangs_once_server("slow1", startup_timeout_seconds=5.0)
        again = McpSessionRuntime(self._plan(edited), ToolRegistry())
        try:
            await again.ensure_started()
            self.assertEqual(again.statuses[0].state, "ready")
        finally:
            await again.aclose()

    async def test_required_server_is_still_waited_for_after_a_timeout(self) -> None:
        plan = self._plan(self._hangs_once_server("needed1", required=True))
        first = McpSessionRuntime(plan, ToolRegistry())
        with self.assertRaises(McpStartupError):
            await first.ensure_started()
        await first.aclose()

        later = McpSessionRuntime(plan, ToolRegistry())
        try:
            await later.ensure_started()
            self.assertEqual(later.statuses[0].state, "ready")
            self.assertIn("needed1", later.available_server_ids)
        finally:
            await later.aclose()


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
