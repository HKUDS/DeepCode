"""Session layer: persistent multi-turn conversation containers.

Sessions are DeepCode's analogue of a Cursor / Codex / Claude Code
"chat": a stable, user-visible identifier that owns

* an ordered transcript of user / assistant messages, and
* zero or more workflow ``Task`` records (paper-to-code runs, chat
  planning runs, etc).

Persistence is JSONL-on-disk (no SQLite, in line with nanobot's session
manager). Default root is ``~/.deepcode/sessions/<session_id>/``.

Public surface:

- :class:`Session`, :class:`SessionMessage`, :class:`SessionTask`,
  :class:`SessionSummary` — data models
- :class:`SessionStore` — read/write API
- :func:`get_default_store` — process-wide singleton used by the
  workflow + UI + CLI layers when they need to attach a task to a
  session.
"""

from core.sessions.models import (
    Session,
    SessionMessage,
    SessionSummary,
    SessionTask,
)
from core.sessions.store import SessionStore, get_default_store

__all__ = [
    "Session",
    "SessionMessage",
    "SessionStore",
    "SessionSummary",
    "SessionTask",
    "get_default_store",
]
