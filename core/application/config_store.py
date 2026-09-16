"""Atomic, cross-process-safe mutation of the user DeepCode configuration."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.config import DeepCodeConfig, home_config_path
from core.private_storage import (
    atomic_write_private_json,
    ensure_private_directory,
    open_private_file,
)


class ConfigRevisionConflict(RuntimeError):
    """The config changed after the caller read the revision it is editing."""


#: Revision of a config file that does not exist yet.
ABSENT_CONFIG_REVISION = "absent"


class ConfigStore:
    """Read/validate/replace the home config without exposing partial writes."""

    _thread_lock = threading.RLock()

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else home_config_path()
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid DeepCode config JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise TypeError("DeepCode config must contain a JSON object")
        return value

    def revision(self) -> str:
        """Content fingerprint for optimistic concurrency on writes.

        Byte-level on purpose: an external editor touching only formatting
        still moves the revision, which is the honest answer — the caller's
        view of the file is stale either way.
        """
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            return ABSENT_CONFIG_REVISION
        return hashlib.sha256(payload).hexdigest()[:16]

    def mutate(
        self,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        with self._thread_lock, _file_lock(self.lock_path):
            if expected_revision is not None and self.revision() != expected_revision:
                # Checked under the lock so the answer cannot race a
                # concurrent writer. Omitting the revision keeps the
                # historical last-write-wins behavior.
                raise ConfigRevisionConflict(
                    "the configuration changed since it was read — reload "
                    "settings and retry"
                )
            current = self.read()
            updated = transform(_json_copy(current))
            if not isinstance(updated, dict):
                raise TypeError("config mutation must return a JSON object")
            DeepCodeConfig.model_validate(updated)
            self._replace(updated)
            return updated

    def _replace(self, value: dict[str, Any]) -> None:
        atomic_write_private_json(self.path, value)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = _json_copy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = _json_copy(value)
    return merged


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    ensure_private_directory(path.parent)
    descriptor = open_private_file(path, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            import msvcrt

            if os.path.getsize(path) == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
