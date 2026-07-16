"""Atomic, cross-process-safe mutation of the user DeepCode configuration."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.config import DeepCodeConfig, home_config_path


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
            raise ValueError("DeepCode config must contain a JSON object")
        return value

    def mutate(
        self,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._thread_lock, _file_lock(self.lock_path):
            current = self.read()
            updated = transform(_json_copy(current))
            if not isinstance(updated, dict):
                raise TypeError("config mutation must return a JSON object")
            DeepCodeConfig.model_validate(updated)
            self._replace(updated)
            return updated

    def _replace(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            _fsync_directory(self.path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


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
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
