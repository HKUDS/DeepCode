"""Application-lifetime ownership for one workspace's Skill sources.

The host owns discovery, policy, and provider caches. Agent sessions receive
their own :class:`SkillRuntime` so per-Turn context is never shared, while all
surfaces still observe the same provider set and invalidation lifecycle.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from core.skills.catalog import LocalSkillProvider, SkillCatalog
from core.skills.config import SkillPolicyStore
from core.skills.monitor import (
    SkillCatalogMonitor,
    SkillChangeCallback,
    provider_change_token,
)
from core.skills.provider import SkillListQuery, SkillProviders, SkillProviderSource

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from core.skills.runtime import SkillRuntime


class SkillCatalogHost:
    """Own the stable Skill catalog backend for one canonical workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        home: str | Path | None = None,
        working_directory: str | Path | None = None,
        include_user: bool = True,
        include_system: bool = True,
        policy: SkillPolicyStore | None = None,
        base_sources: tuple[SkillProviderSource, ...] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.home = (
            Path(home).expanduser().resolve(strict=False) if home is not None else None
        )
        self.working_directory = (
            Path(working_directory).expanduser().resolve(strict=False)
            if working_directory is not None
            else self.workspace
        )
        self.include_user = include_user
        self.include_system = include_system
        self.policy = policy or SkillPolicyStore(self.workspace)
        if base_sources is None:
            local_provider = LocalSkillProvider(
                self.workspace,
                home=self.home,
                working_directory=self.working_directory,
                include_user=self.include_user,
                include_system=self.include_system,
                disabled_loader=self.policy.effective_disabled,
            )
            base_sources = (SkillProviderSource.local(local_provider),)
        if not base_sources:
            raise ValueError("SkillCatalogHost requires at least one base source")
        self._base_sources = base_sources
        self._contributed_sources: tuple[SkillProviderSource, ...] = ()
        self.providers = SkillProviders(base_sources)
        self._source_generation = 0
        self._lock = threading.RLock()

    @property
    def sources(self) -> tuple[SkillProviderSource, ...]:
        return self.providers.sources

    def catalog(self, *, force: bool = False) -> SkillCatalog:
        return self.providers.list(SkillListQuery(force=force))

    def invalidate(self) -> None:
        self.providers.invalidate()

    def change_token(self) -> object:
        """Return provider-owned state used by the application monitor."""

        with self._lock:
            generation = self._source_generation
            sources = self.providers.sources
        return (
            generation,
            tuple(
                (
                    source.kind.value,
                    source.label,
                    provider_change_token(source.provider),
                )
                for source in sources
            ),
        )

    def new_runtime(self) -> SkillRuntime:
        """Create isolated per-Turn state over the shared provider backend."""

        from core.skills.runtime import SkillRuntime

        return SkillRuntime(
            self.workspace,
            policy=self.policy,
            skill_providers=self.providers,
        )

    def set_contributed_sources(
        self,
        sources: tuple[SkillProviderSource, ...],
    ) -> bool:
        """Atomically replace extension-owned sources and invalidate discovery.

        Plugin support can contribute sources through this seam later. The
        Skill runtime does not need to know whether a source came from a local
        directory, a plugin cache, or another provider.
        """

        with self._lock:
            normalized = tuple(sources)
            if normalized == self._contributed_sources:
                return False
            self._contributed_sources = normalized
            self._source_generation += 1
            self.providers.replace_sources(
                (*self._base_sources, *self._contributed_sources)
            )
            return True


HostFactory = Callable[[Path], SkillCatalogHost]
MonitorFactory = Callable[[SkillChangeCallback], SkillCatalogMonitor]
ContributedSourcesLoader = Callable[[Path], tuple[SkillProviderSource, ...]]


class SkillWorkspaceRegistry:
    """Thread-safe application registry of canonical workspace Skill hosts."""

    def __init__(
        self,
        host_factory: HostFactory | None = None,
        *,
        monitor_factory: MonitorFactory | None = None,
    ) -> None:
        self._host_factory = host_factory or SkillCatalogHost
        self._hosts: dict[Path, SkillCatalogHost] = {}
        self._listeners: dict[int, SkillChangeCallback] = {}
        self._contributors: dict[str, ContributedSourcesLoader] = {}
        self._manual_sources: dict[Path, tuple[SkillProviderSource, ...]] = {}
        self._next_listener = 1
        self._lock = threading.RLock()
        factory = monitor_factory or SkillCatalogMonitor
        self._monitor = factory(self._publish_change)

    def get(self, workspace: str | Path) -> SkillCatalogHost:
        canonical = Path(workspace).expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("workspace must be a directory")
        with self._lock:
            host = self._hosts.get(canonical)
            if host is None:
                host = self._host_factory(canonical)
                host.set_contributed_sources(self._load_contributed_sources(canonical))
                self._monitor.register(host)
                self._hosts[canonical] = host
            return host

    def new_runtime(self, workspace: str | Path) -> SkillRuntime:
        return self.get(workspace).new_runtime()

    def set_contributed_sources(
        self,
        workspace: str | Path,
        sources: tuple[SkillProviderSource, ...],
    ) -> None:
        """Replace extension sources and publish one immediate invalidation."""

        host = self.get(workspace)
        with self._lock:
            self._manual_sources[host.workspace] = tuple(sources)
            combined = self._load_contributed_sources(host.workspace)
        changed = host.set_contributed_sources(combined)
        if changed and self._monitor.synchronize(host):
            self._publish_change(host.workspace)

    def invalidate(self, workspace: str | Path) -> bool:
        """Publish a provider-pushed invalidation for a registered workspace."""

        canonical = Path(workspace).expanduser().resolve(strict=False)
        with self._lock:
            host = self._hosts.get(canonical)
        if host is None:
            return False
        host.invalidate()
        self._monitor.synchronize(host)
        self._publish_change(canonical)
        return True

    def register_contributor(
        self,
        contributor_id: str,
        loader: ContributedSourcesLoader,
    ) -> None:
        """Register one independently owned source contributor.

        Contributor IDs provide a lifecycle handle. Each owner can refresh or
        unregister its own providers without a read/modify/write race against
        other extension systems.
        """

        if not contributor_id or any(
            character.isspace() for character in contributor_id
        ):
            raise ValueError(
                "Skill contributor ID must be non-empty without whitespace"
            )
        with self._lock:
            if contributor_id in self._contributors:
                raise RuntimeError(
                    f"Skill contributor is already registered: {contributor_id}"
                )
            self._contributors[contributor_id] = loader
        self.refresh_contributed_sources()

    def unregister_contributor(self, contributor_id: str) -> bool:
        """Remove one contributor and its providers from every workspace."""

        with self._lock:
            removed = self._contributors.pop(contributor_id, None)
        if removed is None:
            return False
        self.refresh_contributed_sources()
        return True

    def refresh_contributed_sources(self) -> tuple[Path, ...]:
        """Re-evaluate extension sources for every registered workspace."""

        with self._lock:
            hosts = tuple(self._hosts.values())
        changed: list[Path] = []
        for host in hosts:
            updated = host.set_contributed_sources(
                self._load_contributed_sources(host.workspace)
            )
            if updated and self._monitor.synchronize(host):
                changed.append(host.workspace)
                self._publish_change(host.workspace)
        return tuple(changed)

    def discard(self, workspace: str | Path) -> None:
        canonical = Path(workspace).expanduser().resolve(strict=False)
        with self._lock:
            self._hosts.pop(canonical, None)
            self._manual_sources.pop(canonical, None)
            self._monitor.unregister(canonical)

    def subscribe(self, listener: SkillChangeCallback) -> int:
        """Subscribe to invalidated workspace catalogs."""

        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def close(self) -> None:
        with self._lock:
            self._hosts.clear()
            self._listeners.clear()
            self._manual_sources.clear()
            self._contributors.clear()
        self._monitor.close()

    def _load_contributed_sources(
        self,
        workspace: Path,
    ) -> tuple[SkillProviderSource, ...]:
        with self._lock:
            loaders = tuple(self._contributors.values())
            manual = self._manual_sources.get(workspace, ())
        contributed = [source for loader in loaders for source in loader(workspace)]
        contributed.extend(manual)
        return tuple(contributed)

    def _publish_change(self, workspace: Path) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener(workspace)
            except Exception:
                logger.exception("Skill workspace change listener failed")


__all__ = [
    "ContributedSourcesLoader",
    "SkillCatalogHost",
    "SkillWorkspaceRegistry",
]
