"""Crash-recoverable filesystem journal for permanent Session deletion."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


DELETION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SessionDeletionTicket:
    """Durable description of a Session quarantined for deletion."""

    session_id: str
    trash_name: str
    requested_at: str


class SessionDeletionJournal:
    """Move canonical data out of discovery before derived state is removed.

    A tiny tombstone survives a process crash.  Startup recovery can therefore
    finish the SQLite cascade without ever re-projecting the quarantined JSONL
    directory back into a Thread.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tombstone_root = root / ".deletions"
        self.trash_root = root / ".trash"

    def pending(self, session_id: str) -> bool:
        return self._tombstone_path(session_id).is_file()

    def read(self, session_id: str) -> SessionDeletionTicket | None:
        path = self._tombstone_path(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ticket = SessionDeletionTicket(
                session_id=str(payload["sessionId"]),
                trash_name=str(payload["trashName"]),
                requested_at=str(payload["requestedAt"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            ticket.session_id != session_id
            or Path(ticket.trash_name).name != ticket.trash_name
            or not ticket.trash_name.startswith(f"{session_id}.")
        ):
            return None
        return ticket

    def list_pending(self) -> tuple[SessionDeletionTicket, ...]:
        if not self.tombstone_root.is_dir():
            return ()
        tickets: list[SessionDeletionTicket] = []
        for path in sorted(self.tombstone_root.glob("*.json")):
            ticket = self.read(path.stem)
            if ticket is not None:
                tickets.append(ticket)
        return tuple(tickets)

    def stage(self, session_id: str, session_directory: Path) -> SessionDeletionTicket:
        existing = self.read(session_id)
        if existing is not None:
            self.ensure_quarantined(existing, session_directory)
            return existing
        if not (session_directory / "session.jsonl").is_file():
            raise FileNotFoundError(f"session not found: {session_id}")

        self.tombstone_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.trash_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        ticket = SessionDeletionTicket(
            session_id=session_id,
            trash_name=f"{session_id}.{uuid.uuid4().hex}.deleting",
            requested_at=datetime.now(UTC).isoformat(),
        )
        tombstone = self._tombstone_path(session_id)
        temporary = tombstone.with_name(f".{tombstone.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "schemaVersion": DELETION_SCHEMA_VERSION,
            "sessionId": ticket.session_id,
            "trashName": ticket.trash_name,
            "requestedAt": ticket.requested_at,
        }
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, tombstone)
            self._fsync_directory(self.tombstone_root)
            self.ensure_quarantined(ticket, session_directory)
            return ticket
        except BaseException:
            temporary.unlink(missing_ok=True)
            if session_directory.exists():
                tombstone.unlink(missing_ok=True)
            raise

    def ensure_quarantined(
        self,
        ticket: SessionDeletionTicket,
        session_directory: Path,
    ) -> None:
        trash = self.trash_root / ticket.trash_name
        if trash.exists():
            return
        if not session_directory.exists():
            return
        self.trash_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(session_directory, trash)
        self._fsync_directory(session_directory.parent)
        self._fsync_directory(self.trash_root)

    def rollback(
        self,
        ticket: SessionDeletionTicket,
        session_directory: Path,
    ) -> None:
        trash = self.trash_root / ticket.trash_name
        if trash.exists() and not session_directory.exists():
            os.replace(trash, session_directory)
            self._fsync_directory(session_directory.parent)
        self._tombstone_path(ticket.session_id).unlink(missing_ok=True)
        self._fsync_directory(self.tombstone_root)

    def finalize(self, ticket: SessionDeletionTicket) -> bool:
        """Purge quarantined bytes; False means startup should retry cleanup."""

        trash = self.trash_root / ticket.trash_name
        try:
            if trash.exists():
                shutil.rmtree(trash)
            self._tombstone_path(ticket.session_id).unlink(missing_ok=True)
            self._fsync_directory(self.trash_root)
            self._fsync_directory(self.tombstone_root)
            return True
        except OSError:
            return False

    def _tombstone_path(self, session_id: str) -> Path:
        return self.tombstone_root / f"{session_id}.json"

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt" or not path.is_dir():
            return
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)


__all__ = [
    "DELETION_SCHEMA_VERSION",
    "SessionDeletionJournal",
    "SessionDeletionTicket",
]
