"""Filesystem-backed session store (JSONL, no SQLite).

The store directory layout is::

    <root>/
      <session_id>/
        session.jsonl   # first line = metadata, subsequent lines = messages
        tasks.jsonl     # one line per attached SessionTask
        settings.json   # optional, per-session preferences

Concurrency model: :class:`threading.RLock` covers in-process
serialisation; we additionally re-read metadata before every write so
two processes editing the same session don't trample each other (the
hot mutation surface — ``append_message`` / ``attach_task`` — is
pure-append to JSONL files, which is atomic at the OS level for small
writes and safe under typical desktop concurrency).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable

from core.sessions.models import (
    Session,
    SessionMessage,
    SessionSummary,
    SessionTask,
    _new_session_id,
    _utcnow_iso,
)


_DEFAULT_ROOT = Path.home() / ".deepcode" / "sessions"


class SessionStore:
    """Read/write JSONL sessions under a configurable root directory."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else _DEFAULT_ROOT
        self._lock = threading.RLock()
        self._cache: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def _session_jsonl(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.jsonl"

    def _tasks_jsonl(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "tasks.jsonl"

    def _settings_json(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "settings.json"

    # ------------------------------------------------------------------
    # Create / read
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        title: str = "",
        session_id: str | None = None,
        metadata: dict | None = None,
    ) -> Session:
        """Create and persist a new empty session.

        Picks an unused ``session_id`` (8-char hex) when not given.
        Always re-rolls if there is a collision on disk.
        """
        with self._lock:
            sid = session_id or _new_session_id()
            attempts = 0
            while self._session_dir(sid).exists():
                sid = _new_session_id()
                attempts += 1
                if attempts > 8:
                    raise RuntimeError("Could not allocate a unique session_id")

            session = Session(
                session_id=sid,
                title=title,
                metadata=dict(metadata or {}),
            )
            self._session_dir(sid).mkdir(parents=True, exist_ok=True)
            self._rewrite_metadata(session)
            self._cache[sid] = session
            return session

    def get_session(self, session_id: str) -> Session | None:
        """Load a session from disk (cached on subsequent calls)."""
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None:
                return cached
            session = self._load(session_id)
            if session is not None:
                self._cache[session_id] = session
            return session

    def list_sessions(
        self,
        *,
        limit: int = 50,
        order: str = "recent",
    ) -> list[SessionSummary]:
        """Return summaries for the ``limit`` most recent sessions."""
        with self._lock:
            if not self.root.exists():
                return []
            summaries: list[SessionSummary] = []
            for entry in self.root.iterdir():
                if not entry.is_dir():
                    continue
                jsonl = entry / "session.jsonl"
                if not jsonl.exists():
                    continue
                metadata = self._read_metadata(entry.name)
                if metadata is None:
                    continue
                # Cheap line-count for messages/tasks; avoids re-parsing
                # full bodies just to populate the listing card.
                message_count = self._count_jsonl(jsonl) - 1  # minus metadata header
                task_count = self._count_jsonl(self._tasks_jsonl(entry.name))
                summaries.append(
                    SessionSummary(
                        session_id=metadata["session_id"],
                        title=metadata.get("title", ""),
                        created_at=metadata.get("created_at", ""),
                        updated_at=metadata.get("updated_at", ""),
                        message_count=max(0, message_count),
                        task_count=task_count,
                    )
                )
            if order == "recent":
                summaries.sort(key=lambda s: s.updated_at, reverse=True)
            elif order == "oldest":
                summaries.sort(key=lambda s: s.updated_at)
            return summaries[: max(1, limit)]

    # ------------------------------------------------------------------
    # Append helpers (hot path)
    # ------------------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        task_id_ref: str | None = None,
        metadata: dict | None = None,
    ) -> SessionMessage | None:
        """Append a transcript message to ``<session_id>/session.jsonl``.

        Returns ``None`` when the session does not exist.
        """
        with self._lock:
            session = self.get_session(session_id)
            if session is None:
                return None
            msg = session.add_message(
                role,
                content,
                task_id_ref=task_id_ref,
                metadata=metadata,
            )
            self._append_jsonl(self._session_jsonl(session_id), msg.to_dict())
            self._rewrite_metadata(session)
            return msg

    def attach_task(
        self,
        session_id: str,
        task_id: str,
        *,
        task_kind: str,
        task_dir: str | os.PathLike,
        status: str = "pending",
        metadata: dict | None = None,
    ) -> SessionTask | None:
        """Record a workflow task as belonging to ``session_id``.

        Idempotent: re-attaching the same ``task_id`` updates its row
        rather than producing a duplicate.
        """
        with self._lock:
            session = self.get_session(session_id)
            if session is None:
                return None
            existing = next((t for t in session.tasks if t.task_id == task_id), None)
            if existing is not None:
                existing.status = status
                existing.task_dir = str(task_dir)
                existing.updated_at = _utcnow_iso()
                if metadata:
                    existing.metadata = {**(existing.metadata or {}), **metadata}
                self._rewrite_tasks(session_id, session.tasks)
                self._rewrite_metadata(session)
                return existing

            task = session.attach_task(
                task_id,
                task_kind=task_kind,
                task_dir=str(task_dir),
                status=status,
                metadata=metadata,
            )
            self._append_jsonl(self._tasks_jsonl(session_id), task.to_dict())
            self._rewrite_metadata(session)
            return task

    def update_task_status(
        self,
        session_id: str,
        task_id: str,
        status: str,
        metadata: dict | None = None,
    ) -> SessionTask | None:
        with self._lock:
            session = self.get_session(session_id)
            if session is None:
                return None
            task = session.update_task_status(task_id, status, metadata=metadata)
            if task is None:
                return None
            self._rewrite_tasks(session_id, session.tasks)
            self._rewrite_metadata(session)
            return task

    # ------------------------------------------------------------------
    # Branch / delete
    # ------------------------------------------------------------------

    def branch_session(
        self,
        source_session_id: str,
        *,
        from_message_index: int,
        title: str | None = None,
    ) -> Session | None:
        """Create a new session forked from the first ``N`` messages.

        Tasks are not copied — branching is for "what if I had answered
        differently here" exploration, where re-running a workflow makes
        sense as a fresh task.
        """
        with self._lock:
            source = self.get_session(source_session_id)
            if source is None:
                return None
            cutoff = max(0, min(from_message_index, len(source.messages)))
            forked = self.create_session(
                title=title or f"branch of {source.title or source.session_id}",
                metadata={
                    "branched_from": source.session_id,
                    "branched_at_message": cutoff,
                },
            )
            for msg in source.messages[:cutoff]:
                self.append_message(
                    forked.session_id,
                    msg.role,
                    msg.content,
                    task_id_ref=None,  # don't carry task refs across branches
                    metadata=msg.metadata,
                )
            return self.get_session(forked.session_id)

    def delete_session(self, session_id: str) -> bool:
        """Remove a session directory recursively. Returns True on success."""
        with self._lock:
            d = self._session_dir(session_id)
            if not d.exists():
                return False
            try:
                for child in sorted(
                    d.rglob("*"), key=lambda p: len(p.parts), reverse=True
                ):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                d.rmdir()
            except OSError:
                return False
            self._cache.pop(session_id, None)
            return True

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_settings(self, session_id: str) -> dict:
        path = self._settings_json(session_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def update_settings(self, session_id: str, **values) -> dict:
        with self._lock:
            current = self.get_settings(session_id)
            current.update(values)
            self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
            self._settings_json(session_id).write_text(
                json.dumps(current, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return current

    # ------------------------------------------------------------------
    # Lookup helpers used by the workflow layer
    # ------------------------------------------------------------------

    def find_session_by_task(self, task_id: str) -> Session | None:
        """Linear scan — fine for the typical session count (<100s)."""
        with self._lock:
            for summary in self.list_sessions(limit=10_000):
                session = self.get_session(summary.session_id)
                if session is None:
                    continue
                if any(t.task_id == task_id for t in session.tasks):
                    return session
            return None

    def list_attached_tasks(self) -> list[tuple[Session, SessionTask]]:
        """Return every (session, task) pair stored. Used at backend boot
        to rebuild the in-memory ``WorkflowTask`` cache after a restart.
        """
        out: list[tuple[Session, SessionTask]] = []
        for summary in self.list_sessions(limit=10_000):
            session = self.get_session(summary.session_id)
            if session is None:
                continue
            for task in session.tasks:
                out.append((session, task))
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_jsonl(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str))
            fh.write("\n")

    def _read_metadata(self, session_id: str) -> dict | None:
        path = self._session_jsonl(session_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
                if not first:
                    return None
                data = json.loads(first)
                if data.get("_type") != "metadata":
                    return None
                return data
        except (OSError, json.JSONDecodeError):
            return None

    def _rewrite_metadata(self, session: Session) -> None:
        """Rewrite the metadata header without touching the message tail.

        Strategy: read all message lines, then rewrite the whole file
        with the fresh metadata as line one. This is acceptable because
        sessions are small (~hundreds of lines max) and metadata updates
        are rare compared to message appends.
        """
        path = self._session_jsonl(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        message_lines: list[str] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("_type") == "metadata":
                        continue
                    message_lines.append(line)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(session.metadata_line())
            fh.write("\n")
            for line in message_lines:
                fh.write(line)
                fh.write("\n")
        os.replace(tmp, path)

    def _rewrite_tasks(self, session_id: str, tasks: Iterable[SessionTask]) -> None:
        path = self._tasks_jsonl(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for task in tasks:
                fh.write(json.dumps(task.to_dict(), ensure_ascii=False, default=str))
                fh.write("\n")
        os.replace(tmp, path)

    def _count_jsonl(self, path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        count += 1
        except OSError:
            return 0
        return count

    def _load(self, session_id: str) -> Session | None:
        sess_path = self._session_jsonl(session_id)
        if not sess_path.exists():
            return None
        metadata: dict | None = None
        messages: list[SessionMessage] = []
        try:
            with sess_path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if parsed.get("_type") == "metadata":
                        metadata = parsed
                    elif parsed.get("_type") == "message":
                        messages.append(SessionMessage.from_dict(parsed))
        except OSError:
            return None
        if metadata is None:
            return None

        tasks: list[SessionTask] = []
        tasks_path = self._tasks_jsonl(session_id)
        if tasks_path.exists():
            try:
                with tasks_path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if parsed.get("_type") == "task":
                            tasks.append(SessionTask.from_dict(parsed))
            except OSError:
                pass

        session = Session(
            session_id=str(metadata.get("session_id") or session_id),
            title=str(metadata.get("title", "")),
            created_at=str(metadata.get("created_at") or _utcnow_iso()),
            updated_at=str(metadata.get("updated_at") or _utcnow_iso()),
            messages=messages,
            tasks=tasks,
            metadata=dict(metadata.get("metadata") or {}),
        )
        return session


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_DEFAULT_STORE: SessionStore | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_store() -> SessionStore:
    """Return (and lazily create) the shared store at the default root."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_STORE is None:
                env_root = os.environ.get("DEEPCODE_SESSIONS_DIR")
                _DEFAULT_STORE = SessionStore(env_root) if env_root else SessionStore()
    return _DEFAULT_STORE


__all__ = [
    "SessionStore",
    "get_default_store",
]
