"""Tests for the web AgentChatService — workspace persistence & revive.

P2-L5c: a chat's workspace must be a durable fact of the session (stored in
metadata), so a backend restart revives the chat in the SAME directory —
including custom workspaces — and the sidebar can display it. Offline:
scripted provider, tmp session store.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.agent_setup as agent_setup  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _Provider:
    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        return LLMResponse(content="ok", finish_reason="stop")


class _Profile:
    model = "fake-model"


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (_Provider(), _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import core.sessions.store as store_mod

    monkeypatch.setattr(store_mod, "_DEFAULT_STORE", None)


def _service():
    from new_ui.backend.services.agent_chat_service import AgentChatService

    return AgentChatService()


def test_create_persists_workspace_metadata(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    svc = _service()

    custom = str(tmp_path / "proj")
    card = svc.create_chat(workspace=custom)
    stored = svc.store.get_session(card["session_id"])
    assert stored.metadata.get("workspace") == custom
    assert card["workspace"] == custom

    default_card = svc.create_chat()
    stored2 = svc.store.get_session(default_card["session_id"])
    # Default workspace is derived from the session id and persisted too.
    assert stored2.metadata.get("workspace") == default_card["workspace"]
    assert default_card["session_id"] in default_card["workspace"]


def test_revive_uses_recorded_workspace_and_history(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    svc = _service()
    custom = str(tmp_path / "proj")
    sid = svc.create_chat(workspace=custom)["session_id"]
    svc.store.append_message(sid, "user", "hi")
    svc.store.append_message(sid, "assistant", "hello")

    # Simulate a backend restart: live registry gone, store persists.
    svc._live.clear()
    svc._meta.clear()

    agent = svc.get_agent(sid)
    assert svc._meta[sid]["workspace"] == custom  # NOT the derived default
    assert len(agent.history) == 2  # transcript reloaded


def test_list_chats_includes_workspace_and_filters_kind(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    svc = _service()
    card = svc.create_chat(title="web one")
    svc.store.append_message(card["session_id"], "user", "x")
    # A TUI-kind session must not leak into the web sidebar.
    svc.store.create_session(title="tui-side", metadata={"kind": "tui"})

    rows = svc.list_chats()
    assert [r["session_id"] for r in rows] == [card["session_id"]]
    assert rows[0]["workspace"] == card["workspace"]


def test_rename_chat(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    svc = _service()
    sid = svc.create_chat()["session_id"]
    assert svc.rename_chat(sid, "  Renamed  ") is True
    assert svc.store.get_session(sid).title == "Renamed"
    assert svc.rename_chat("nope", "x") is False


class _ErrorProvider:
    """Returns a connection error as data on both the plain and streaming
    paths — exactly how the real provider signals an LLM outage."""

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        return self._error()

    async def chat_stream_with_retry(self, **kwargs: Any):
        return self._error()

    @staticmethod
    def _error():
        return LLMResponse(
            content="Error calling LLM: Connection error.",
            finish_reason="error",
            error_kind="connection",
        )


class _RaisingProvider:
    """Raises unexpectedly — the session must still close the turn."""

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        raise RuntimeError("boom")

    async def chat_stream_with_retry(self, **kwargs: Any):
        raise RuntimeError("boom")


def test_errored_turn_persists_user_but_not_assistant(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **kw: (_ErrorProvider(), _Profile()),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import core.sessions.store as store_mod

    monkeypatch.setattr(store_mod, "_DEFAULT_STORE", None)
    svc = _service()
    sid = svc.create_chat()["session_id"]

    async def _drive():
        return [e async for e in svc.run_turn(sid, "hi there")]

    events = asyncio.run(_drive())
    kinds = [e["msg"]["type"] for e in events]
    assert "error" in kinds  # the live error event was emitted
    assert kinds[-1] == "task_complete"  # turn always terminates

    roles = [m["role"] for m in svc.transcript(sid)]
    # User message kept (turn is retryable); the error is NOT a fake assistant reply.
    assert roles == ["user"]


def test_raising_provider_still_terminates_turn(monkeypatch, tmp_path):
    """Robustness: an unexpected exception in the runner must not hang the
    stream — the turn closes with error + task_complete."""
    import asyncio

    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **kw: (_RaisingProvider(), _Profile()),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import core.sessions.store as store_mod

    monkeypatch.setattr(store_mod, "_DEFAULT_STORE", None)
    svc = _service()
    sid = svc.create_chat()["session_id"]

    async def _drive():
        return [e async for e in svc.run_turn(sid, "hi")]

    events = asyncio.wait_for(_drive(), timeout=10)
    result = asyncio.run(events)
    kinds = [e["msg"]["type"] for e in result]
    assert "error" in kinds
    assert kinds[-1] == "task_complete"


def test_delete_chat_removes_session_and_live_agent(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    svc = _service()
    sid = svc.create_chat()["session_id"]
    svc.get_agent(sid)  # materialize a live agent
    assert sid in svc._live

    assert svc.delete_chat(sid) is True
    assert sid not in svc._live and sid not in svc._meta
    assert svc.store.get_session(sid) is None
    assert svc.delete_chat(sid) is False  # already gone
