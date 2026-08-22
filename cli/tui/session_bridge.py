"""Small read-side helper over the persistent session store for the TUI.

Turn persistence and resume rehydration live in the application layer
(``turn_service`` records turns; ``session_runtime`` reloads canonical
history into the agent) — this module deliberately does *not* duplicate
them. What remains here is the store-backed metadata the REPL itself
needs:

- the identity of the current session (validated against the store);
- its recorded origin workspace, for cross-directory resume hints;
- first-message titling for flows that bypass the Thread rename path.

Session *listing* is not here either: the resume picker goes through
``application.threads.list`` (the same service the Desktop uses), which
scopes by directory with resolved paths instead of re-implementing the
filter over raw store rows.
"""

from __future__ import annotations

import os

from core.sessions import SessionStore, get_default_store


class SessionBridge:
    """Store-backed metadata for one TUI conversation."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        session_id: str,
        workspace: str | None = None,
    ) -> None:
        self.store = store or get_default_store()
        self.workspace = os.path.abspath(workspace) if workspace else None
        existing = self.store.get_session(session_id)
        if existing is None:
            raise ValueError(f"no such session: {session_id}")
        self.session_id = existing.session_id

    def set_title_from(self, first_message: str) -> None:
        """Title the session after its first message (like Claude Code)."""
        title = first_message.strip().splitlines()[0][:60]
        try:
            session = self.store.get_session(self.session_id)
            if session is not None and not session.title and title:
                self.store.rename_session(self.session_id, title)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass

    def stored_workspace(self) -> str | None:
        """This session's recorded workspace (for cross-directory hints)."""
        stored = self.store.get_session(self.session_id)
        if stored is None:
            return None
        return (stored.metadata or {}).get("workspace") or None
