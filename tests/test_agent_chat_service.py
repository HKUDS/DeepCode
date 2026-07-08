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
