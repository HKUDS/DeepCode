"""Offline, verifiable snapshots of the database and canonical runtime state.

Locks reuse existing application, Session and credential mutation boundaries.
A restore journal blocks application startup until an interrupted restore is
resumed. Project working trees and installed executables are not restored.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app_server.service_state import ServiceFiles
from core.config import deepcode_home, home_config_path
from core.file_lock import FileLease
from core.persistence.database import Database
from core.private_storage import (
    atomic_write_private_json,
    ensure_private_directory,
    open_existing_private_file,
    open_private_file,
)
from core.version import __version__

SESSION_INTERNAL = {".locks", ".running", ".activity", ".store.lock"}


@dataclass(frozen=True)
class StatePaths:
    database: Path
    sessions: Path
    config: Path
    credentials: Path
    revisions: Path

    @classmethod
    def current(cls, files: ServiceFiles, *, sessions: Path | None = None):
        layout = files.directory / "state-layout.json"
        if layout.exists():
            with os.fdopen(
                open_existing_private_file(layout), "r", encoding="utf-8"
            ) as stream:
                value = json.loads(stream.read(16385))
            if (
                not isinstance(value, dict)
                or value.get("schemaVersion") != 1
                or value.get("database") != str(files.database)
            ):
                raise ValueError("Invalid saved service data layout")
            fields = {
                name: Path(value[name])
                for name in (
                    "database",
                    "sessions",
                    "config",
                    "credentials",
                    "revisions",
                )
            }
            if not all(path.is_absolute() for path in fields.values()):
                raise ValueError("Service data paths must be absolute")
            if (
                sessions is not None
                and sessions.expanduser().resolve() != fields["sessions"]
            ):
                raise ValueError(
                    "The requested Session directory differs from the recorded service layout"
                )
            return cls(**fields)
        home = deepcode_home().resolve()
        return cls(
            files.database,
            (
                sessions
                or Path(
                    os.environ.get("DEEPCODE_SESSIONS_DIR") or str(home / "sessions")
                )
            )
            .expanduser()
            .resolve(),
            home_config_path().resolve(),
            home / "credentials.json",
            home / "provider_revisions",
        )

    def targets(self) -> dict[str, Path]:
        return {
            "database.sqlite3": self.database,
            "sessions": self.sessions,
            "config.json": self.config,
            "credentials.json": self.credentials,
            "revisions": self.revisions,
            "mcp-credentials.json": self.credentials.parent / "auth" / "mcp.json",
        }


def _excluded(relative: Path) -> bool:
    parts = relative.parts
    if parts[:1] == ("sessions",) and len(parts) > 1:
        return (
            parts[1] in SESSION_INTERNAL
            or parts[1] == "index.db"
            or parts[1].startswith("index.db-")
        )
    return parts == ("revisions", "write.lock")


def _files(root: Path, *, prefix=Path()):
    if root.is_symlink():
        raise ValueError("State snapshots do not follow symlinks")
    if not root.exists():
        return
    if root.is_file():
        yield prefix, root
        return
    if not root.is_dir():
        raise ValueError("State snapshots require regular files and directories")
    for child in sorted(root.iterdir()):
        relative = prefix / child.name
        if not _excluded(relative):
            yield from _files(child, prefix=relative)


def _digest(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(open_existing_private_file(path), "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _copy_file(source: Path, target: Path) -> None:
    with os.fdopen(open_existing_private_file(source), "rb") as reader:
        with os.fdopen(
            open_private_file(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "wb"
        ) as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())


def _sync_directory(directory: Path):
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _sync_tree(directory: Path):
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            _sync_tree(child)
    _sync_directory(directory)


@contextmanager
def _offline(paths: StatePaths, *, extra_sessions=()):
    files = ServiceFiles(paths.database)
    with ExitStack() as stack:

        def lock(path):
            lease = FileLease.acquire(path, shared=False, blocking=False)
            if lease is None:
                raise ValueError(
                    "State is in use. Stop DeepCode services, CLI/TUI and other writers before backing up or restoring."
                )
            stack.enter_context(lease)

        lock(files.directory / "management.lock")
        lock(files.lock)
        lock(paths.database.with_name(paths.database.name + ".application.lock"))
        lock(paths.database.with_name(paths.database.name + ".migration.lock"))
        lock(paths.config.with_suffix(paths.config.suffix + ".lock"))
        lock(paths.credentials.with_suffix(paths.credentials.suffix + ".lock"))
        mcp_credentials = paths.targets()["mcp-credentials.json"]
        lock(mcp_credentials.with_suffix(mcp_credentials.suffix + ".lock"))
        lock(paths.revisions / "write.lock")
        lock(paths.sessions / ".store.lock")
        ids = set(extra_sessions) | {
            item.name
            for item in paths.sessions.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        }
        for identity in sorted(ids):
            if Path(identity).name != identity or identity in {"", ".", ".."}:
                raise ValueError("Invalid Session identity in snapshot")
            for directory in (".activity", ".running", ".locks"):
                lock(paths.sessions / directory / f"{identity}.lock")
        yield


def _snapshot(
    paths: StatePaths, destination: Path, *, require_idle: bool = True
) -> dict:
    if destination.exists():
        raise ValueError("Snapshot destination already exists")
    for name, source in paths.targets().items():
        if destination == source or (
            name in {"sessions", "revisions"} and destination.is_relative_to(source)
        ):
            raise ValueError("Snapshot destination overlaps runtime data")
    ensure_private_directory(destination.parent)
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination.parent))
    try:
        present = []
        for name, source in paths.targets().items():
            if not source.exists():
                continue
            present.append(name)
            if name == "database.sqlite3":
                target = staging / name
                os.close(
                    open_private_file(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                )
                with (
                    closing(
                        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
                    ) as reader,
                    closing(sqlite3.connect(target)) as writer,
                ):
                    if require_idle:
                        _require_idle_database(reader)
                    reader.backup(writer)
                    writer.execute("PRAGMA journal_mode=DELETE")
                    if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Snapshot database integrity check failed")
                with target.open("rb+") as stream:
                    os.fsync(stream.fileno())
            else:
                for relative, original in _files(source, prefix=Path(name)):
                    _copy_file(original, staging / relative)
        inventory = {str(relative): _digest(path) for relative, path in _files(staging)}
        manifest = {
            "schemaVersion": 1,
            "runtimeVersion": __version__,
            "createdAt": datetime.now(UTC).isoformat(),
            "paths": {name: str(path) for name, path in paths.targets().items()},
            "present": present,
            "files": inventory,
        }
        atomic_write_private_json(staging / "manifest.json", manifest)
        _sync_tree(staging)
        os.rename(staging, destination)
        _sync_directory(destination.parent)
        return {
            "snapshot": str(destination),
            "fileCount": len(inventory),
            "runtimeVersion": __version__,
            "paths": manifest["paths"],
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def create_snapshot(paths: StatePaths, destination: Path) -> dict:
    with _offline(paths):
        if Database(paths.database).restore_marker.exists():
            raise ValueError(
                "Resume the pending restore before making another snapshot"
            )
        return _snapshot(paths, destination.expanduser().absolute())


def _require_idle_database(connection):
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'"
        ).fetchone()
        and connection.execute(
            "SELECT 1 FROM turns WHERE status IN ('queued', 'running', 'waiting_approval') LIMIT 1"
        ).fetchone()
    ):
        raise ValueError(
            "Runtime snapshots require all Turns to be settled. Drain or explicitly cancel pending work first."
        )


def _manifest(snapshot: Path, paths: StatePaths) -> dict:
    with os.fdopen(
        open_existing_private_file(snapshot / "manifest.json"), "r", encoding="utf-8"
    ) as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Snapshot manifest exceeds 8 MiB")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or value.get("paths")
        != {name: str(path) for name, path in paths.targets().items()}
    ):
        raise ValueError(
            "Snapshot format or original data locations do not match this service"
        )
    expected = value.get("files")
    if (
        not isinstance(expected, dict)
        or not isinstance(value.get("present"), list)
        or set(value["present"]) - paths.targets().keys()
    ):
        raise ValueError("Invalid snapshot manifest")
    actual = {
        str(relative): _digest(path)
        for relative, path in _files(snapshot)
        if str(relative) != "manifest.json"
    }
    if actual != expected:
        raise ValueError("Snapshot contents failed checksum verification")
    for relative in expected:
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] not in paths.targets()
        ):
            raise ValueError("Snapshot contains an invalid destination")
        if path.parts[0] not in {"sessions", "revisions"} and len(path.parts) != 1:
            raise ValueError("Snapshot file destination is invalid")
    if "database.sqlite3" in expected:
        with closing(
            sqlite3.connect(
                (snapshot / "database.sqlite3").as_uri() + "?mode=ro&immutable=1",
                uri=True,
            )
        ) as connection:
            _require_idle_database(connection)
    return value


def _install_file(source: Path, target: Path):
    temporary = target.with_name("." + target.name + ".restore-new")
    # A failed earlier attempt may have left only its staging file.
    temporary.unlink(missing_ok=True)
    try:
        _copy_file(source, temporary)
        os.replace(temporary, target)
        _sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def restore_snapshot(paths: StatePaths, snapshot: Path, *, replace_data: bool) -> dict:
    if not replace_data:
        raise ValueError(
            "Restoring replaces runtime data after the snapshot. Pass --replace-data explicitly."
        )
    snapshot = snapshot.expanduser().absolute()
    if any(snapshot.is_relative_to(root) for root in (paths.sessions, paths.revisions)):
        raise ValueError(
            "The snapshot must be outside the runtime directories being restored"
        )
    manifest = _manifest(snapshot, paths)
    extra = {
        Path(name).parts[1]
        for name in manifest["files"]
        if name.startswith("sessions/")
        and len(Path(name).parts) > 2
        and not Path(name).parts[1].startswith(".")
    }
    marker = Database(paths.database).restore_marker
    with _offline(paths, extra_sessions=extra):
        # Recheck after locking; nothing has been modified yet.
        manifest = _manifest(snapshot, paths)
        identity = _digest(snapshot / "manifest.json")["sha256"]
        if marker.exists():
            with os.fdopen(
                open_existing_private_file(marker), "r", encoding="utf-8"
            ) as stream:
                journal = json.load(stream)
            if (
                journal.get("snapshot") != str(snapshot)
                or journal.get("manifestHash") != identity
            ):
                raise ValueError(
                    "A different restore is pending. Resume its original snapshot first."
                )
        else:
            before = (
                paths.database.parent
                / "backups"
                / ("before-restore-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
            )
            _snapshot(paths, before, require_idle=False)
            journal = {
                "schemaVersion": 1,
                "snapshot": str(snapshot),
                "manifestHash": identity,
                "beforeRestore": str(before),
            }
            atomic_write_private_json(marker, journal)
        wanted = set(manifest["files"])
        for name, target in paths.targets().items():
            if name in {"sessions", "revisions"}:
                # Preserve lock inodes, preventing a competing process from
                # acquiring a newly created replacement lock during restore.
                for relative, current in list(_files(target, prefix=Path(name))):
                    if str(relative) not in wanted:
                        current.unlink()
            elif name not in manifest["present"]:
                target.unlink(missing_ok=True)
        for relative in sorted(wanted):
            parts = Path(relative).parts
            target = paths.targets()[parts[0]].joinpath(*parts[1:])
            _install_file(snapshot / relative, target)
        # Disposable indexes/WAL must not replay changes from the replaced DB.
        for path in (paths.database, paths.sessions / "index.db"):
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        (paths.sessions / "index.db").unlink(missing_ok=True)
        for root in (paths.sessions, paths.revisions):
            _sync_tree(root)
        # Pending external login flows must never become valid again merely
        # because a backup restored their old generation values.
        if paths.credentials.exists():
            data = json.loads(paths.credentials.read_text())
            import secrets

            data["loginGenerations"] = {
                key: secrets.token_hex(24) for key in data.get("loginGenerations", {})
            }
            atomic_write_private_json(paths.credentials, data)
        atomic_write_private_json(
            Database(paths.database).restore_recovery_marker,
            {"schemaVersion": 1, "snapshot": str(snapshot)},
        )
        marker.unlink()
        _sync_directory(marker.parent)
        return {
            "restored": str(snapshot),
            "beforeRestore": journal["beforeRestore"],
            "phase": "stopped",
        }
