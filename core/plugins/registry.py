"""Atomic registry for linked local Agent Plugin packages."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.config import deepcode_home
from core.plugins.domain import (
    PluginAlreadyRegisteredError,
    PluginRegistration,
    PluginRegistrationNotFoundError,
    PluginRegistryError,
    PluginSource,
    PluginSourceKind,
    ResolvedPlugin,
)
from core.plugins.formats.agent_plugins_v1 import validate_plugin_name
from core.private_storage import ensure_private_directory, open_private_file

PLUGIN_REGISTRY_SCHEMA_VERSION = 1
MAX_REGISTERED_PLUGINS = 256


class LocalPluginRegistry:
    """Persist package registrations without copying or deleting source code."""

    _thread_lock = threading.RLock()

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve(strict=False)
            if path is not None
            else deepcode_home() / "plugins" / "registry.json"
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def list(self) -> tuple[PluginRegistration, ...]:
        if not self.path.is_file():
            return ()
        try:
            with self.path.open(encoding="utf-8") as stream:
                raw = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginRegistryError(f"Invalid Plugin registry: {exc}") from exc
        return _parse_registry(raw)

    def add(
        self, plugin: ResolvedPlugin, *, enabled: bool = True
    ) -> PluginRegistration:
        registration = PluginRegistration(
            installation_id=f"plg_{uuid.uuid4().hex[:24]}",
            name=plugin.name,
            source=PluginSource(PluginSourceKind.LINKED_DIRECTORY, plugin.root),
            enabled=enabled,
        )

        def mutate(current: list[PluginRegistration]) -> list[PluginRegistration]:
            if any(item.name == registration.name for item in current):
                raise PluginAlreadyRegisteredError(
                    f"Plugin is already registered: {registration.name}"
                )
            if any(item.source.path == registration.source.path for item in current):
                raise PluginAlreadyRegisteredError(
                    f"Plugin directory is already registered: {registration.source.path}"
                )
            if len(current) >= MAX_REGISTERED_PLUGINS:
                raise PluginRegistryError(
                    f"Plugin registry is limited to {MAX_REGISTERED_PLUGINS} entries"
                )
            return [*current, registration]

        self._mutate(mutate)
        return registration

    def set_enabled(self, selector: str, enabled: bool) -> None:
        def mutate(current: list[PluginRegistration]) -> list[PluginRegistration]:
            target = _select(current, selector)
            return [
                PluginRegistration(
                    item.installation_id,
                    item.name,
                    item.source,
                    enabled=enabled,
                )
                if item.installation_id == target.installation_id
                else item
                for item in current
            ]

        self._mutate(mutate)

    def remove(self, selector: str) -> PluginRegistration:
        removed: PluginRegistration | None = None

        def mutate(current: list[PluginRegistration]) -> list[PluginRegistration]:
            nonlocal removed
            removed = _select(current, selector)
            return [
                item
                for item in current
                if item.installation_id != removed.installation_id
            ]

        self._mutate(mutate)
        assert removed is not None
        return removed

    def change_token(self) -> object:
        try:
            stat = self.path.stat()
            payload = self.path.read_bytes()
            return stat.st_mtime_ns, stat.st_size, hashlib.sha256(payload).hexdigest()
        except OSError:
            return None, None, None

    def _mutate(
        self,
        operation: Callable[
            [list[PluginRegistration]],
            list[PluginRegistration],
        ],
    ) -> None:
        ensure_private_directory(self.path.parent)
        with self._thread_lock, _file_lock(self.lock_path):
            updated = operation(list(self.list()))
            self._replace(tuple(updated))

    def _replace(self, registrations: tuple[PluginRegistration, ...]) -> None:
        value = {
            "schemaVersion": PLUGIN_REGISTRY_SCHEMA_VERSION,
            "registrations": [
                {
                    "installationId": item.installation_id,
                    "name": item.name,
                    "enabled": item.enabled,
                    "source": {
                        "kind": item.source.kind.value,
                        "path": str(item.source.path),
                    },
                }
                for item in registrations
            ],
        }
        payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = open_private_file(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
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


def _parse_registry(raw: Any) -> tuple[PluginRegistration, ...]:
    if not isinstance(raw, dict):
        raise PluginRegistryError("Plugin registry must contain a JSON object")
    if raw.get("schemaVersion") != PLUGIN_REGISTRY_SCHEMA_VERSION:
        raise PluginRegistryError(
            f"Unsupported Plugin registry schemaVersion: {raw.get('schemaVersion')!r}"
        )
    entries = raw.get("registrations")
    if not isinstance(entries, list):
        raise PluginRegistryError("Plugin registry registrations must be an array")
    if len(entries) > MAX_REGISTERED_PLUGINS:
        raise PluginRegistryError(
            f"Plugin registry exceeds {MAX_REGISTERED_PLUGINS} entries"
        )

    result: list[PluginRegistration] = []
    installation_ids: set[str] = set()
    names: set[str] = set()
    paths: set[Path] = set()
    for raw_item in entries:
        if not isinstance(raw_item, dict):
            raise PluginRegistryError("Plugin registry entries must be objects")
        installation_id = raw_item.get("installationId")
        name = raw_item.get("name")
        enabled = raw_item.get("enabled")
        source = raw_item.get("source")
        if not isinstance(installation_id, str):
            raise PluginRegistryError("Plugin installationId must be a string")
        try:
            validate_plugin_name(name)
        except ValueError as exc:
            raise PluginRegistryError(str(exc)) from exc
        if not isinstance(enabled, bool):
            raise PluginRegistryError("Plugin enabled must be boolean")
        if not isinstance(source, dict):
            raise PluginRegistryError("Plugin source must be an object")
        if source.get("kind") != PluginSourceKind.LINKED_DIRECTORY.value:
            raise PluginRegistryError(
                f"Unsupported Plugin source kind: {source.get('kind')!r}"
            )
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise PluginRegistryError("Linked Plugin source path must be a string")
        path = Path(raw_path).expanduser().resolve(strict=False)
        try:
            registration = PluginRegistration(
                installation_id=installation_id,
                name=name,
                source=PluginSource(PluginSourceKind.LINKED_DIRECTORY, path),
                enabled=enabled,
            )
        except ValueError as exc:
            raise PluginRegistryError(str(exc)) from exc
        if installation_id in installation_ids:
            raise PluginRegistryError(
                f"Duplicate Plugin installation ID: {installation_id}"
            )
        if name in names:
            raise PluginRegistryError(f"Duplicate registered Plugin name: {name}")
        if path in paths:
            raise PluginRegistryError(f"Duplicate registered Plugin path: {path}")
        installation_ids.add(installation_id)
        names.add(name)
        paths.add(path)
        result.append(registration)
    return tuple(result)


def _select(
    registrations: list[PluginRegistration],
    selector: str,
) -> PluginRegistration:
    if not isinstance(selector, str) or not selector.strip():
        raise PluginRegistrationNotFoundError("Plugin selector must not be empty")
    clean = selector.strip()
    match = next(
        (
            item
            for item in registrations
            if item.installation_id == clean or item.name == clean
        ),
        None,
    )
    if match is None:
        raise PluginRegistrationNotFoundError(f"Plugin is not registered: {clean}")
    return match


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
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


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "MAX_REGISTERED_PLUGINS",
    "PLUGIN_REGISTRY_SCHEMA_VERSION",
    "LocalPluginRegistry",
]
