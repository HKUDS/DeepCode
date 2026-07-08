"""Agent chat service — live AgentSessions behind the web API (P2, L5).

The web analogue of the TUI: each chat is one persistent
:class:`~core.events.session.AgentSession` (kernel + native tools + P1
permissions, assembled by the same :func:`core.agent_setup.build_agent_session`
the CLI uses, so frontends cannot drift). This service owns:

- the registry of live sessions (keyed by the *stored* session id, so a chat
  survives a backend restart — history reloads from ``core.sessions``);
- turn execution as an async stream of serialized SQ/EQ events, which the
  WebSocket layer forwards verbatim (§3 event-sourcing first: the web UI is
  a pure event consumer, exactly like the terminal renderer);
- transcript persistence + first-message titling, mirroring the TUI's
  SessionBridge semantics.

This is *additive*: the legacy workflow pipeline endpoints are untouched.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from core.agent_setup import build_agent_session
from core.events import AgentSession, Interrupt, UserInput, serialize_event
from core.sessions import get_default_store

_CHAT_KIND = "agent_chat"


def _default_workspace_root() -> Path:
    # Sibling of the workflow outputs: deepcode_lab/chats/<session_id>/
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "deepcode_lab" / "chats"


class AgentChatService:
    """Own live agent sessions and stream their events."""

    def __init__(self) -> None:
        self.store = get_default_store()
        self._live: dict[str, AgentSession] = {}
        self._meta: dict[str, dict[str, Any]] = {}  # sid -> {model, workspace}
        self._lock = threading.Lock()

    # -- session lifecycle ----------------------------------------------------

    def create_chat(
        self,
        *,
        title: str = "",
        model: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Create a stored chat session + its live agent; return its card."""
        stored = self.store.create_session(
            title=title, metadata={"kind": _CHAT_KIND, "model": model or ""}
        )
        sid = stored.session_id
        ws = (
            os.path.abspath(workspace)
            if workspace
            else str(_default_workspace_root() / sid)
        )
        # Persist the workspace so a backend restart revives the chat in the
        # SAME directory — critical for custom workspaces (P2-L5c).
        self.store.update_metadata(sid, {"workspace": ws})
        agent, resolved_model, engine = build_agent_session(
            workspace=ws, model=model, streaming=True
        )
        with self._lock:
            self._live[sid] = agent
            self._meta[sid] = {"model": resolved_model, "workspace": ws}
        return {
            "session_id": sid,
            "model": resolved_model,
            "workspace": ws,
            "permission_mode": engine.mode.value,
            "title": stored.title,
        }

    def _revive(self, session_id: str) -> AgentSession:
        """Rebuild a live agent for a stored chat (post-restart resume)."""
        stored = self.store.get_session(session_id)
        if stored is None:
            raise KeyError(f"no such session: {session_id}")
        meta = stored.metadata or {}
        model = meta.get("model") or None
        # Prefer the recorded workspace; fall back to the derived default
        # for chats stored before workspaces were persisted.
        ws = meta.get("workspace") or str(_default_workspace_root() / session_id)
        agent, resolved_model, _engine = build_agent_session(
            workspace=ws, model=model, streaming=True
        )
        agent.load_history(
            [
                {"role": m.role, "content": m.content}
                for m in stored.messages
                if m.role in ("user", "assistant") and m.content
            ]
        )
        with self._lock:
            self._live[session_id] = agent
            self._meta[session_id] = {"model": resolved_model, "workspace": ws}
        return agent

    def get_agent(self, session_id: str) -> AgentSession:
        with self._lock:
            agent = self._live.get(session_id)
        return agent if agent is not None else self._revive(session_id)

    # -- queries ---------------------------------------------------------------

    def list_chats(self, limit: int = 50) -> list[dict[str, Any]]:
        """Chat-kind sessions only (workflow sessions keep their own listing)."""
        out: list[dict[str, Any]] = []
        for summary in self.store.list_sessions(limit=200):
            stored = self.store.get_session(summary.session_id)
            if stored is None or (stored.metadata or {}).get("kind") != _CHAT_KIND:
                continue
            out.append(
                {
                    "session_id": summary.session_id,
                    "title": summary.title or "(untitled)",
                    "updated_at": summary.updated_at,
                    "message_count": summary.message_count,
                    "workspace": (stored.metadata or {}).get("workspace")
                    or str(_default_workspace_root() / summary.session_id),
                }
            )
            if len(out) >= limit:
                break
        return out

    def transcript(self, session_id: str) -> list[dict[str, Any]]:
        stored = self.store.get_session(session_id)
        if stored is None:
            raise KeyError(f"no such session: {session_id}")
        return [
            {"role": m.role, "content": m.content, "timestamp": m.timestamp}
            for m in stored.messages
            if m.role in ("user", "assistant")
        ]

    # -- turns -------------------------------------------------------------------

    async def run_turn(
        self, session_id: str, text: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one turn; yield each event as a JSON-ready dict."""
        agent = self.get_agent(session_id)
        stored = self.store.get_session(session_id)
        if stored is not None and not stored.title:
            first_line = text.strip().splitlines()[0][:60]
            if first_line:
                self.store.rename_session(session_id, first_line)

        final_text: str | None = None
        async for event in agent.run_stream(UserInput(text=text)):
            if event.msg.type == "task_complete":
                final_text = event.msg.final_text
            yield serialize_event(event)

        # Persist the visible turn (best-effort, never breaks the stream).
        try:
            self.store.append_message(session_id, "user", text)
            if final_text:
                self.store.append_message(session_id, "assistant", final_text)
        except Exception:  # noqa: BLE001
            pass

    async def interrupt(self, session_id: str) -> None:
        with self._lock:
            agent = self._live.get(session_id)
        if agent is not None:
            await agent.submit(Interrupt())


agent_chat_service = AgentChatService()
