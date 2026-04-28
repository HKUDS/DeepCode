"""Session service shim — thin re-export of :mod:`core.sessions`.

Historically this module shipped its own in-memory ``SessionService``
that was never wired to any FastAPI route. The real implementation now
lives under :mod:`core.sessions` (JSONL persistence, sharable between
the UI backend and the CLI). We keep this file so existing imports
``from services.session_service import session_store`` keep working.
"""

from __future__ import annotations

from core.sessions import (
    Session,
    SessionMessage,
    SessionStore,
    SessionSummary,
    SessionTask,
    get_default_store,
)

# Process-wide singleton; created lazily on first attribute access.
session_store: SessionStore = get_default_store()


__all__ = [
    "Session",
    "SessionMessage",
    "SessionStore",
    "SessionSummary",
    "SessionTask",
    "session_store",
]
