"""MCP calls use the real shared HTTP/WS host with a deterministic provider."""

from __future__ import annotations

import asyncio

import pytest

from cli import mcp_server
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.providers.base import LLMResponse
from tests.test_exec_cli import _patch

pytestmark = pytest.mark.usefixtures("shared_cli_service")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    class Provider:
        def get_default_model(self):
            return "fake-model"

        async def chat_with_retry(self, **_kwargs):
            return LLMResponse(content="completed in service", finish_reason="stop")

    _patch(monkeypatch, Provider())
    path = tmp_path / "project"
    path.mkdir()
    app = DeepCodeApplication.open()
    try:
        app.projects.add(str(path), trust_state=TrustState.TRUSTED)
    finally:
        app.close()
    return path


def test_list_tools_exposes_both():
    assert [t.name for t in (mcp_server._DEEPCODE_TOOL, mcp_server._REPLY_TOOL)] == [
        "deepcode",
        "deepcode-reply",
    ]
    assert mcp_server.build_server().name == "deepcode"


def test_deepcode_and_reply_share_a_durable_session(workspace):
    first, identity = asyncio.run(
        mcp_server._handle_deepcode({"prompt": "first", "workspace": str(workspace)})
    )
    assert first[0].text == "completed in service"
    assert identity["stop_reason"] == "completed"
    # A new MCP server has no process-local Session registry to recover.
    mcp_server.build_server()
    second, continued = asyncio.run(
        mcp_server._handle_reply(
            {"session_id": identity["session_id"], "prompt": "second"}
        )
    )
    assert second[0].text == "completed in service"
    assert continued["session_id"] == identity["session_id"]
    from core.sessions import SessionStore

    assert len(SessionStore().list_sessions()) == 1


def test_reply_unknown_session_errors():
    content, result = asyncio.run(
        mcp_server._handle_reply({"session_id": "nope", "prompt": "x"})
    )
    assert result.get("error") and "Error:" in content[0].text


def test_missing_prompt_errors():
    content, result = asyncio.run(mcp_server._handle_deepcode({"prompt": " "}))
    assert "required" in content[0].text and result["error"] == "missing prompt"


def test_mcp_requires_existing_workspace_trust(tmp_path):
    content, result = asyncio.run(
        mcp_server._handle_deepcode(
            {"prompt": "write a file", "workspace": str(tmp_path)}
        )
    )
    assert result["error"] == "PERMISSION_DENIED"
    assert "trust" in content[0].text.lower()


def test_call_tool_dispatch_unknown_tool():
    async def scenario():
        from mcp import types

        server = mcp_server.build_server()
        handler = server.request_handlers[types.CallToolRequest]
        return await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name="bogus", arguments={}),
            )
        )

    assert "unknown tool" in asyncio.run(scenario()).root.content[0].text
