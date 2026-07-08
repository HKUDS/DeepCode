"""Bridge between the live AgentSession and the persistent session store.

Responsibilities (and nothing else):

- record each completed turn (user text + assistant reply) into
  :mod:`core.sessions` — JSONL on disk, listed via the SQLite index;
- resume: load a stored transcript back into an
  :class:`~core.events.session.AgentSession` as chat history.

Resume fidelity: the store keeps the *visible* conversation (user/assistant
text), not the internal tool-call messages — resuming restores conversational
context, and the agent re-reads files as needed. This matches the resume
semantics of comparable CLIs and keeps the on-disk format tool-agnostic.
"""

from __future__ import annotations

from core.events.session import AgentSession
from core.sessions import SessionStore, SessionSummary, get_default_store


class SessionBridge:
    """Persist turns of one TUI conversation and support resume."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        session_id: str | None = None,
        title: str = "",
    ) -> None:
        self.store = store or get_default_store()
        if session_id is not None:
            existing = self.store.get_session(session_id)
            if existing is None:
                raise ValueError(f"no such session: {session_id}")
            self.session_id = existing.session_id
        else:
            self.session_id = self.store.create_session(title=title).session_id

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

    def list_recent(self, limit: int = 20) -> list[SessionSummary]:
        """Recent *resumable* sessions — empty ones are noise, not history."""
        rows = self.store.list_sessions(limit=max(limit * 3, 30))
        return [s for s in rows if s.message_count > 0][:limit]
