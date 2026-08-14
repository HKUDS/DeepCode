"""User-only credential storage for named LLM connections."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from core.config import deepcode_home
from core.file_lock import exclusive_file_lock
from core.private_storage import (
    ensure_private_directory,
    open_existing_private_file,
    open_private_file,
)


def default_credentials_path() -> Path:
    return deepcode_home() / "credentials.json"


class CredentialStore:
    """Atomic 0600 JSON storage whose values are never projected to clients."""

    _thread_lock = threading.RLock()

    def __init__(self, path: Path | str | None = None) -> None:
        selected = (
            Path(path).expanduser() if path is not None else default_credentials_path()
        )
        # ``resolve`` follows a final symlink and erases the identity that the
        # secure read path must inspect. ``abspath`` keeps that final component
        # intact while still making lock and temporary paths cwd-independent.
        self.path = Path(os.path.abspath(os.fspath(selected)))
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def get(self, connection_id: str) -> str | None:
        value = self._read().get("connections", {}).get(connection_id)
        return value if isinstance(value, str) and value else None

    def configured(self, connection_id: str) -> bool:
        return self.get(connection_id) is not None

    def set(self, connection_id: str, api_key: str) -> None:
        clean = api_key.strip()
        if not clean:
            raise ValueError("api key must not be empty")
        self._mutate(
            lambda data: {
                **data,
                "version": 1,
                "connections": {
                    **_connections(data),
                    connection_id: clean,
                },
            }
        )

    def clear(self, connection_id: str) -> bool:
        removed = False

        def transform(data: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            connections = _connections(data)
            removed = connections.pop(connection_id, None) is not None
            return {**data, "version": 1, "connections": connections}

        self._mutate(transform)
        return removed

    def revision(self) -> str:
        """Return a non-secret fingerprint suitable for runtime invalidation."""

        try:
            descriptor = open_existing_private_file(self.path)
        except FileNotFoundError:
            return "missing"
        try:
            metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        raw = f"{self.path}:{metadata.st_mtime_ns}:{metadata.st_size}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _read(self) -> dict[str, Any]:
        try:
            descriptor = open_existing_private_file(self.path)
        except FileNotFoundError:
            return {"version": 1, "connections": {}}
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid DeepCode credentials JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise TypeError("DeepCode credentials must contain a JSON object")
        if value.get("version", 1) != 1:
            raise ValueError("unsupported DeepCode credentials version")
        if not isinstance(value.get("connections", {}), dict):
            raise TypeError("credentials.connections must be an object")
        return value

    def _mutate(self, transform) -> None:
        with self._thread_lock, exclusive_file_lock(self.lock_path):
            updated = transform(self._read())
            self._replace(updated)

    def _replace(self, value: dict[str, Any]) -> None:
        ensure_private_directory(self.path.parent)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = open_private_file(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        try:
            payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _connections(data: dict[str, Any]) -> dict[str, str]:
    value = data.get("connections", {})
    return dict(value) if isinstance(value, dict) else {}


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["CredentialStore", "default_credentials_path"]
