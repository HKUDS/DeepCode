"""Bridge between the live AgentSession and the persistent session store.

Responsibilities (and nothing else):

- record each completed turn (user text + assistant reply) into
  :mod:`core.sessions` — JSONL on disk, listed via the SQLite index;
- resume: load a stored transcript back into an
  :class:`~core.events.session.AgentSession` as chat history;
- scope the resume picker to the current directory.

Directory scoping follows the convention Claude Code and Codex converged on
(DEEPCODE_V2_MASTER_PLAN.md P2-L5c): storage stays *central* under
``~/.deepcode/sessions`` (never files dropped into the project), but the
*view* is per-directory — a new session records its ``workspace`` in
metadata, and the default listing shows only sessions born in the current
workspace. ``include_all`` lifts the filter (legacy sessions without a
recorded workspace, and other frontends' sessions, appear only there).

Resume fidelity: the store keeps the *visible* conversation (user/assistant
text), not the internal tool-call messages — resuming restores conversational
context, and the agent re-reads files as needed. This matches the resume
semantics of comparable CLIs and keeps the on-disk format tool-agnostic.
"""

from __future__ import annotations

import os

from core.events.session import AgentSession
from core.sessions import SessionStore, SessionSummary, get_default_store

_KIND = "tui"


class SessionBridge:
    """Persist turns of one TUI conversation and support scoped resume."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        session_id: str | None = None,
        title: str = "",
        workspace: str | None = None,
    ) -> None:
        self.store = store or get_default_store()
        self.workspace = os.path.abspath(workspace) if workspace else None
        if session_id is not None:
            existing = self.store.get_session(session_id)
            if existing is None:
                raise ValueError(f"no such session: {session_id}")
            self.session_id = existing.session_id
        else:
            metadata: dict = {"kind": _KIND}
            if self.workspace:
                metadata["workspace"] = self.workspace
            self.session_id = self.store.create_session(
                title=title, metadata=metadata
            ).session_id

    # -- write path ----------------------------------------------------------

    def record_turn(self, user_text: str, assistant_text: str | None) -> None:
        """Persist one completed turn. Errors here must never kill the REPL."""
        try:
            self.store.append_message(self.session_id, "user", user_text)
            if assistant_text:
                self.store.append_message(self.session_id, "assistant", assistant_text)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass

    def set_title_from(self, first_message: str) -> None:
        """Title the session after its first message (like Claude Code)."""
        title = first_message.strip().splitlines()[0][:60]
        try:
            session = self.store.get_session(self.session_id)
            if session is not None and not session.title and title:
                self.store.rename_session(self.session_id, title)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass

    # -- read path -----------------------------------------------------------

    def load_into(self, agent: AgentSession) -> int:
        """Load this session's transcript into ``agent``; return turn count."""
        stored = self.store.get_session(self.session_id)
        if stored is None:
            return 0
        history = [
            {"role": m.role, "content": m.content}
            for m in stored.messages
            if m.role in ("user", "assistant") and m.content
        ]
        agent.load_history(history)
        return len(history)

    def workspace_of(self, session_id: str) -> str | None:
        """The workspace a stored session was created in, if recorded."""
        stored = self.store.get_session(session_id)
        if stored is None:
            return None
        return (stored.metadata or {}).get("workspace") or None

    def stored_workspace(self) -> str | None:
        """This session's recorded workspace (for cross-directory hints)."""
        return self.workspace_of(self.session_id)

    def list_recent(
        self, limit: int = 20, *, include_all: bool = False
    ) -> list[SessionSummary]:
        """Recent *resumable* sessions, scoped to this bridge's workspace.

        Default view lists only sessions whose recorded workspace matches
        (the per-directory picker). ``include_all=True`` lists every
        non-empty session regardless of origin. Empty sessions are noise,
        not history, and never appear.

        The workspace lives in each session's metadata line, so scoping
        reads sessions through the store's cache — same pattern the web
        chat listing uses; fine at CLI session counts.
        """
        rows = [
            s
            for s in self.store.list_sessions(limit=max(limit * 4, 60))
            if s.message_count > 0
        ]
        if include_all or self.workspace is None:
            return rows[:limit]
        return [s for s in rows if self.workspace_of(s.session_id) == self.workspace][
            :limit
        ]
