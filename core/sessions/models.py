"""Session-layer data models.

These are plain dataclasses (not Pydantic) for two reasons:

1. The session is a hot-write surface — every chat turn appends to disk.
   Skipping validation keeps the path predictable.
2. The on-disk format is intentionally schema-light JSONL so external
   tools (``jq``, ``tail -f``) can consume sessions without importing
   DeepCode.

Each session lives in its own directory under the store root:

::

    <root>/<session_id>/
        session.jsonl     # metadata header line + message lines
        tasks.jsonl       # one line per attached workflow task
        settings.json     # optional: session-scoped preferences
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    """Short uuid4 hex for new sessions.

    8 chars matches the ``task_id`` short form used by
    ``workflows/environment.py`` so users can correlate them visually.
    """
    return uuid.uuid4().hex[:8]


@dataclass(slots=True)
class SessionMessage:
    """One entry on the session transcript."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=_utcnow_iso)
    task_id_ref: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "_type": "message",
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.task_id_ref:
            d["task_id_ref"] = self.task_id_ref
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionMessage":
        return cls(
            role=str(raw.get("role", "user")),
            content=str(raw.get("content", "")),
            timestamp=str(raw.get("timestamp") or _utcnow_iso()),
            task_id_ref=raw.get("task_id_ref"),
            metadata=raw.get("metadata") or None,
        )


@dataclass(slots=True)
class SessionTask:
    """A workflow task that has been attached to a session."""

    task_id: str
    task_kind: str  # "paper" | "chat" | "url" | "repo" | "requirement"
    task_dir: str
    status: str = "pending"  # mirrors WorkflowTask.status
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "_type": "task",
            "task_id": self.task_id,
            "task_kind": self.task_kind,
            "task_dir": self.task_dir,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SessionTask":
        return cls(
            task_id=str(raw.get("task_id", "")),
            task_kind=str(raw.get("task_kind", "unknown")),
            task_dir=str(raw.get("task_dir", "")),
            status=str(raw.get("status", "pending")),
            created_at=str(raw.get("created_at") or _utcnow_iso()),
            updated_at=str(raw.get("updated_at") or _utcnow_iso()),
            metadata=raw.get("metadata") or None,
        )


@dataclass
class Session:
    """An open conversation container.

    Sessions are append-only on disk. In-memory mutation
    (:meth:`add_message`, :meth:`attach_task`) updates ``updated_at`` so
    the listing endpoint can sort by recency without re-reading every
    file.
    """

    session_id: str = field(default_factory=_new_session_id)
    title: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    messages: list[SessionMessage] = field(default_factory=list)
    tasks: list[SessionTask] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_message(
        self,
        role: str,
        content: str,
        *,
        task_id_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionMessage:
        msg = SessionMessage(
            role=role,
            content=content,
            task_id_ref=task_id_ref,
            metadata=metadata,
        )
        self.messages.append(msg)
        self.updated_at = msg.timestamp
        if not self.title and role == "user":
            self.title = self._title_from(content)
        return msg

    def attach_task(
        self,
        task_id: str,
        *,
        task_kind: str,
        task_dir: str,
        status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> SessionTask:
        task = SessionTask(
            task_id=task_id,
            task_kind=task_kind,
            task_dir=task_dir,
            status=status,
            metadata=metadata,
        )
        self.tasks.append(task)
        self.updated_at = task.updated_at
        return task

    def update_task_status(self, task_id: str, status: str) -> SessionTask | None:
        for t in self.tasks:
            if t.task_id == task_id:
                t.status = status
                t.updated_at = _utcnow_iso()
                self.updated_at = t.updated_at
                return t
        return None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def metadata_line(self) -> str:
        payload = {
            "_type": "metadata",
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _title_from(content: str) -> str:
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        return (first_line[:60] + "…") if len(first_line) > 60 else first_line

    def summary(self) -> "SessionSummary":
        return SessionSummary(
            session_id=self.session_id,
            title=self.title,
            created_at=self.created_at,
            updated_at=self.updated_at,
            message_count=len(self.messages),
            task_count=len(self.tasks),
        )


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Compact view used by list endpoints."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    task_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "Session",
    "SessionMessage",
    "SessionSummary",
    "SessionTask",
]
