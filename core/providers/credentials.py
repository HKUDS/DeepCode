"""User-only credential storage for named LLM connections."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from core.config import deepcode_home
from core.file_lock import exclusive_file_lock
from core.private_storage import (
    atomic_write_private_json,
    open_existing_private_file,
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

        def transform(data):
            self._invalidate_login(data, connection_id)
            data.setdefault("accounts", {}).pop(connection_id, None)
            return {
                **data,
                "version": 1,
                "connections": {**_connections(data), connection_id: clean},
            }

        self._mutate(transform)

    def clear(self, connection_id: str) -> bool:
        removed = False

        def transform(data: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            connections = _connections(data)
            self._invalidate_login(data, connection_id)
            data.setdefault("accounts", {}).pop(connection_id, None)
            removed = connections.pop(connection_id, None) is not None
            return {**data, "version": 1, "connections": connections}

        self._mutate(transform)
        return removed

    def oauth_credential(self, connection_id: str) -> tuple[str | None, str | None]:
        data = self._read()
        account = data.get("accounts", {}).get(connection_id, {})
        key = data.get("connections", {}).get(connection_id)
        if (
            not isinstance(account, dict)
            or account.get("provider") != "openrouter"
            or not isinstance(account.get("accountId"), str)
            or not account["accountId"]
            or not isinstance(key, str)
            or not key
        ):
            return None, None
        return key, account.get("accountId")

    @staticmethod
    def _invalidate_login(data, connection_id):
        generation = secrets.token_hex(24)
        data.setdefault("loginGenerations", {})[connection_id] = generation
        return generation

    def begin_login(self, connection_id: str) -> str:
        generation = None

        def transform(data):
            nonlocal generation
            generation = self._invalidate_login(data, connection_id)
            return data

        self._mutate(transform)
        return generation

    def cancel_login(self, connection_id: str, generation: str) -> None:
        def transform(data):
            if data.get("loginGenerations", {}).get(connection_id) == generation:
                self._invalidate_login(data, connection_id)
            return data

        self._mutate(transform)

    def complete_login(
        self, connection_id: str, generation: str, *, api_key: str, account_id: str
    ) -> None:
        if not api_key or not account_id or len(account_id) > 256:
            raise ValueError("The provider returned an invalid account identity")

        def transform(data):
            if data.get("loginGenerations", {}).get(connection_id) != generation:
                raise ValueError("This login was cancelled or superseded")
            existing = data.get("accounts", {}).get(connection_id)
            if existing and existing.get("accountId") != account_id:
                raise ValueError(
                    "A different account was selected. Disconnect the existing account before switching."
                )
            data.setdefault("connections", {})[connection_id] = api_key
            data.setdefault("accounts", {})[connection_id] = {
                "provider": "openrouter",
                "accountId": account_id,
            }
            self._invalidate_login(data, connection_id)
            return data

        self._mutate(transform)

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
        for field in ("accounts", "loginGenerations"):
            if not isinstance(value.get(field, {}), dict):
                raise TypeError(f"credentials.{field} must be an object")
        return value

    def _mutate(self, transform) -> None:
        with self._thread_lock, exclusive_file_lock(self.lock_path):
            updated = transform(self._read())
            self._replace(updated)

    def _replace(self, value: dict[str, Any]) -> None:
        atomic_write_private_json(self.path, value)


def _connections(data: dict[str, Any]) -> dict[str, str]:
    value = data.get("connections", {})
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["CredentialStore", "default_credentials_path"]
