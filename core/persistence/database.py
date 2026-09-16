"""SQLite connection and transaction boundary."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from core.config import deepcode_home
from core.file_lock import exclusive_file_lock
from core.persistence.migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    current_version,
    migrate,
)
from core.private_storage import (
    ensure_private_directory,
    ensure_private_file,
    open_private_file,
)


def default_database_path() -> Path:
    return deepcode_home() / "state" / "deepcode.sqlite3"


# Database files whose ACLs this process already repaired — see
# Database._harden_files for why re-hardening on every open is wrong.
_hardened_files: set[str] = set()


class Database:
    """Connection factory; no process-global mutable connection is retained."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve() if path else default_database_path()
        )

    @property
    def restore_marker(self) -> Path:
        return self.path.with_name(self.path.name + ".restore.json")

    @property
    def restore_recovery_marker(self) -> Path:
        return self.path.with_name(self.path.name + ".restored.json")

    def initialize(self, *, target_version: int = LATEST_SCHEMA_VERSION) -> None:
        ensure_private_directory(self.path.parent)
        with exclusive_file_lock(self._migration_lock_path()):
            if self.restore_marker.exists():
                raise RuntimeError(
                    "A state restore is pending. Resume it with deepcode service restore before starting the application."
                )
            had_existing_database = self._has_existing_database()
            installed = self.schema_version()
            if installed > target_version:
                raise MigrationError(
                    f"database schema {installed} is newer than supported {target_version}"
                )
            connection = self._connect()
            try:
                self._enable_wal(connection)
                installed_version = current_version(connection)
                if had_existing_database and installed_version != target_version:
                    self._backup_before_migration(
                        connection,
                        source_version=installed_version,
                        target_version=target_version,
                    )
                migrate(connection, target_version)
            finally:
                connection.close()
                self._harden_files()

    def schema_version(self) -> int:
        """Read the installed schema without creating or migrating it."""

        if not self._has_existing_database():
            return 0
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            return current_version(connection)
        finally:
            connection.close()

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def _connect(self) -> sqlite3.Connection:
        ensure_private_directory(self.path.parent)
        descriptor = open_private_file(self.path, os.O_RDWR | os.O_CREAT)
        os.close(descriptor)
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = NORMAL")
        self._harden_files()
        return connection

    def _harden_files(self) -> None:
        """Repair the database files' permissions at most once per process.

        ``ensure_private_file`` re-applies the Windows ACL (3 ``icacls`` spawns
        per file), and this runs on every connect, read and transaction: the
        suite spawned tens of thousands of ``icacls`` processes, which is both
        slow and — because ``CreateProcess`` can stall for minutes under that
        much spawn pressure — a way to wedge the process. It also contradicts
        the "restrict at creation, not per open" rule this module already
        follows (``ensure_private_directory`` restricts only what it created)
        and the ACL tests pin (``tests/test_private_storage_acl_once.py``).

        Keyed by path, deliberately without re-checking identity: a sibling that
        SQLite deletes and recreates (``-wal``/``-shm``) is created inside the
        already restricted directory, so it inherits the restricted ACL —
        re-running ``icacls`` on every recreation is exactly the per-open
        re-hardening this avoids. The parent directory is restricted first, in
        ``_connect``/``initialize``, which is what makes that inheritance hold.
        """

        for suffix in ("", "-wal", "-shm", "-journal"):
            path = Path(f"{self.path}{suffix}")
            key = os.fspath(path)
            if key in _hardened_files:
                continue
            try:
                path.lstat()
            except OSError:
                # Not created yet: it will be seen (and repaired) the first time
                # it exists, not on every open before that.
                continue
            ensure_private_file(path)
            _hardened_files.add(key)

    def _migration_lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.migration.lock")

    def _has_existing_database(self) -> bool:
        try:
            return self.path.stat().st_size > 0
        except OSError:
            return False

    def _backup_before_migration(
        self,
        connection: sqlite3.Connection,
        *,
        source_version: int,
        target_version: int,
    ) -> Path:
        backup_directory = self.path.parent / "backups"
        ensure_private_directory(backup_directory)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        suffix = self.path.suffix or ".sqlite3"
        filename = (
            f"{self.path.stem}.pre-v{source_version}-to-v{target_version}-"
            f"{timestamp}-{uuid.uuid4().hex[:8]}{suffix}"
        )
        backup_path = backup_directory / filename
        temporary = backup_path.with_name(f".{backup_path.name}.tmp")
        try:
            destination = sqlite3.connect(temporary)
            try:
                with destination:
                    connection.backup(destination)
                    result = destination.execute("PRAGMA quick_check").fetchone()
                    if not result or result[0] != "ok":
                        raise sqlite3.DatabaseError(
                            "migration backup failed SQLite quick_check"
                        )
            finally:
                # A sqlite3 connection context commits or rolls back but does not
                # close the handle. Windows requires it closed before os.replace.
                destination.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, backup_path)
            return backup_path
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()
            self._harden_files()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a short write transaction and commit or fully roll it back."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()
            self._harden_files()
