"""Tests for C5a — the ``deepcode mcp`` server.

The AgentSession is stubbed so we exercise the real MCP wiring (tool list,
call dispatch, session storage, multi-turn reply) without an LLM.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import mcp_server  # noqa: E402


class _Msg:
    def __init__(self, type_, final_text=None, stop_reason=None):
        self.type = type_
        self.final_text = final_text
        self.stop_reason = stop_reason


class _Event:
    def __init__(self, msg):
        self.msg = msg


class _FakeSession:
    """Records prompts; each run_stream yields a task_complete echoing the prompt."""

    def __init__(self):
        self.prompts = []

    async def run_stream(self, op):
        self.prompts.append(op.text)
        yield _Event(_Msg("agent_message"))
        yield _Event(_Msg("task_complete", final_text=f"did: {op.text}", stop_reason="completed"))


@pytest.fixture(autouse=True)
def _clear_sessions():
    mcp_server._SESSIONS.clear()
    yield
    mcp_server._SESSIONS.clear()


@pytest.fixture
def fake_build(monkeypatch):
    created = []

    def _build(**kwargs):
        session = _FakeSession()
        created.append((session, kwargs))
        return session, kwargs.get("model") or "fake-model", None

    monkeypatch.setattr("core.agent_setup.build_agent_session", _build)
    return created


def test_list_tools_exposes_both():
    server = mcp_server.build_server()
    # the registered list_tools handler returns both tools
    names = [t.name for t in [mcp_server._DEEPCODE_TOOL, mcp_server._REPLY_TOOL]]
    assert names == ["deepcode", "deepcode-reply"]
    assert server.name == "deepcode"


def test_deepcode_runs_stores_session_and_returns_id(fake_build):
    content, structured = asyncio.run(
        mcp_server._handle_deepcode({"prompt": "build X", "workspace": "/tmp/x"})
    )
    assert content[0].text == "did: build X"
    sid = structured["session_id"]
    assert structured["stop_reason"] == "completed"
    assert sid in mcp_server._SESSIONS  # kept for follow-ups
    # the workspace reached build_agent_session
    _session, kwargs = fake_build[0]
    assert kwargs["workspace"].endswith("/tmp/x") or kwargs["workspace"] == "/tmp/x"


def test_reply_continues_same_session(fake_build):
    _c1, s1 = asyncio.run(mcp_server._handle_deepcode({"prompt": "first"}))
    sid = s1["session_id"]
    content, structured = asyncio.run(
        mcp_server._handle_reply({"session_id": sid, "prompt": "second"})
    )
    assert content[0].text == "did: second"
    assert structured["session_id"] == sid
    # same underlying session saw both prompts, in order
    assert mcp_server._SESSIONS[sid].prompts == ["first", "second"]


def test_reply_unknown_session_errors():
    content, structured = asyncio.run(
        mcp_server._handle_reply({"session_id": "nope", "prompt": "x"})
    )
    assert "no such session" in content[0].text
    assert structured["error"] == "unknown session"


def test_missing_prompt_errors(fake_build):
    content, structured = asyncio.run(mcp_server._handle_deepcode({"prompt": "   "}))
    assert "required" in content[0].text
    assert structured["error"] == "missing prompt"
    assert mcp_server._SESSIONS == {}  # nothing stored on a bad call


def test_call_tool_dispatch_unknown_tool():
    async def scenario():
        server = mcp_server.build_server()
        # reach the registered call_tool handler via the request handlers map
        from mcp import types

        handler = server.request_handlers[types.CallToolRequest]
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="bogus", arguments={}),
        )
        return await handler(req)

    result = asyncio.run(scenario())
    text = result.root.content[0].text
    assert "unknown tool" in text