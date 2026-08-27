"""Application-lifetime monitoring for mutable Skill provider catalogs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.skills.provider import SkillChangeTokenProvider

logger = logging.getLogger(__name__)


class MonitoredSkillHost(Protocol):
    """Narrow host surface required by the monitor."""

    workspace: Path

    def change_token(self) -> object:
        """Return catalog-affecting provider state."""

    def invalidate(self) -> None:
        """Invalidate provider caches."""


SkillChangeCallback = Callable[[Path], None]


@dataclass(slots=True)
class _Entry:
    host: MonitoredSkillHost
    token: object
    error_logged: bool = False
    generation: int = 0


class SkillCatalogMonitor:
    """One portable monitor thread shared by every registered workspace.

    Providers expose bounded change tokens instead of leaking filesystem paths
    into the application or UI. This makes monitoring deterministic in tests,
    works in the packaged sidecar without native watcher dependencies, and
    leaves room for plugin providers to supply their own token implementation.
    """

    def __init__(
        self,
        on_change: SkillChangeCallback,
        *,
        interval_seconds: float = 0.5,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Skill monitor interval must be positive")
        self._on_change = on_change
        self._interval_seconds = interval_seconds
        self._entries: dict[Path, _Entry] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, host: MonitoredSkillHost) -> None:
        token = host.change_token()
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("Skill monitor is closed")
            self._entries[host.workspace] = _Entry(host=host, token=token)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="deepcode-skill-monitor",
                    daemon=True,
                )
                self._thread.start()

    def unregister(self, workspace: str | Path) -> None:
        canonical = Path(workspace).expanduser().resolve(strict=False)
        with self._lock:
            self._entries.pop(canonical, None)

    def synchronize(self, host: MonitoredSkillHost) -> bool:
        """Accept a host change already published through an explicit API."""

        token = host.change_token()
        with self._lock:
            entry = self._entries.get(host.workspace)
            if entry is not None and entry.host is host:
                changed = entry.token != token
                entry.token = token
                entry.error_logged = False
                entry.generation += 1
                return changed
        return False

    def poll_once(self) -> tuple[Path, ...]:
        """Check every host once; public for deterministic contract tests."""

        with self._lock:
            entries = tuple(
                (workspace, entry, entry.generation)
                for workspace, entry in self._entries.items()
            )
        changed: list[Path] = []
        for workspace, entry, generation in entries:
            try:
                token = entry.host.change_token()
            except Exception:
                with self._lock:
                    current = self._entries.get(workspace)
                    current_generation = (
                        current.generation if current is not None else None
                    )
                    active = current is entry and current_generation == generation
                    should_log = active and not entry.error_logged
                    if active:
                        entry.error_logged = True
                if should_log:
                    logger.warning(
                        "Unable to inspect Skill catalog changes for %s",
                        workspace,
                        exc_info=True,
                    )
                continue
            with self._lock:
                current = self._entries.get(workspace)
                current_generation = current.generation if current is not None else None
                active = current is entry and current_generation == generation
                if active:
                    entry.error_logged = False
                if not active or token == entry.token:
                    continue
                entry.token = token
            entry.host.invalidate()
            changed.append(workspace)
            try:
                self._on_change(workspace)
            except Exception:
                logger.exception("Skill catalog change observer failed")
        return tuple(changed)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
            self._entries.clear()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.poll_once()


def provider_change_token(provider: object) -> object:
    """Return a stable token for providers that opt into host monitoring."""

    if isinstance(provider, SkillChangeTokenProvider):
        return provider.skill_change_token()
    return None


__all__ = ["SkillCatalogMonitor", "SkillChangeCallback", "provider_change_token"]
