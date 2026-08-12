"""Application-lifetime lifecycle for linked Agent Plugin packages."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from core.mcp.models import McpServerSource, ResolvedMcpServer
from core.plugins.domain import (
    PluginComponentKind,
    PluginComponentStatus,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginLoadResult,
    PluginRegistration,
    PluginSnapshot,
    PluginValidationError,
)
from core.plugins.formats.agent_plugins_v1 import (
    MCP_RESOURCE,
    PLUGIN_MANIFEST_RESOURCE,
    SKILLS_RESOURCE,
)
from core.plugins.mcp import (
    load_agent_plugin_mcp,
    plugin_policy_key,
    plugin_server_id,
)
from core.plugins.registry import LocalPluginRegistry
from core.plugins.resolver import resolve_plugin
from core.plugins.skill_provider import PluginSkillProvider
from core.private_storage import ensure_private_directory
from core.skills.host import SkillWorkspaceRegistry
from core.skills.provider import SkillProviderSource

logger = logging.getLogger(__name__)
PluginChangeListener = Callable[[PluginSnapshot], None]
PLUGIN_SKILL_CONTRIBUTOR_ID = "agent-plugins-v1"


class LocalPluginHost:
    """Resolve inert packages and contribute only supported components.

    Discovery reads manifests and Skill resources. It never imports Plugin
    code, starts a process, activates MCP, or executes hooks.
    """

    def __init__(
        self,
        skill_hosts: SkillWorkspaceRegistry,
        *,
        registry: LocalPluginRegistry | None = None,
        interval_seconds: float = 0.5,
        monitor: bool = True,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Plugin monitor interval must be positive")
        self.skill_hosts = skill_hosts
        self.registry = registry or LocalPluginRegistry()
        self._interval_seconds = interval_seconds
        self._listeners: dict[int, PluginChangeListener] = {}
        self._next_listener = 1
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._closed = False
        self._snapshot = self._build_snapshot()
        self._change_token = self._registry_and_component_token()
        self._thread: threading.Thread | None = None
        self.skill_hosts.register_contributor(
            PLUGIN_SKILL_CONTRIBUTOR_ID,
            self.sources,
        )
        if monitor:
            self._thread = threading.Thread(
                target=self._run,
                name="deepcode-plugin-monitor",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> PluginSnapshot:
        with self._lock:
            return self._snapshot

    def sources(self, workspace: Path) -> tuple[SkillProviderSource, ...]:
        sources: list[SkillProviderSource] = []
        for result in self.snapshot().plugins:
            plugin = result.plugin
            if (
                not result.registration.enabled
                or plugin is None
                or result.error is not None
            ):
                continue
            skills = plugin.package.component(PluginComponentKind.SKILLS)
            if skills is None or skills.status is not PluginComponentStatus.READY:
                continue
            provider = PluginSkillProvider(
                plugin,
                installation_id=result.registration.installation_id,
                workspace=workspace,
            )
            sources.append(
                SkillProviderSource.for_authority(
                    provider.authority,
                    provider,
                    label=f"Plugin {plugin.name}",
                )
            )
        return tuple(sources)

    def mcp_servers(self, workspace: Path) -> tuple[ResolvedMcpServer, ...]:
        """Resolve active Plugin MCP declarations for one Agent session.

        Package discovery remains inert. This method is called only while an
        Agent session is being assembled, at which point the persistent,
        installation-scoped ``PLUGIN_DATA`` directory is materialized. MCP
        processes themselves are still owned and started by ``McpSessionRuntime``.
        """

        workspace = Path(workspace).expanduser().resolve(strict=False)
        contributions: list[ResolvedMcpServer] = []
        for result in self.snapshot().plugins:
            registration = result.registration
            plugin = result.plugin
            if not registration.enabled or plugin is None or result.error is not None:
                continue
            component = plugin.package.component(PluginComponentKind.MCP)
            if component is None or component.status is not PluginComponentStatus.READY:
                continue
            parsed = load_agent_plugin_mcp(
                plugin.root,
                plugin_schema=plugin.package.schema,
            )
            if parsed.fatal:
                logger.warning(
                    "Plugin '%s' MCP component changed after discovery; skipped",
                    plugin.name,
                )
                continue
            plugin_data = ensure_private_directory(
                self.registry.path.parent / "data" / registration.installation_id
            ).resolve(strict=False)
            for template in parsed.servers:
                policy_key = plugin_policy_key(plugin.name, template.name)
                contributions.append(
                    ResolvedMcpServer(
                        server_id=plugin_server_id(plugin.name, template.name),
                        name=policy_key,
                        source=McpServerSource.PLUGIN,
                        definition=template.definition,
                        config_dir=plugin.root,
                        workspace=workspace,
                        plugin_id=registration.installation_id,
                        plugin_root=plugin.root,
                        plugin_data=plugin_data,
                        policy_key=policy_key,
                        plugin_server_name=template.name,
                    )
                )
        return tuple(contributions)

    def refresh(self) -> PluginSnapshot:
        """Synchronously resolve the registry and publish one atomic change."""

        updated = self._build_snapshot()
        with self._lock:
            previous = self._snapshot
            self._snapshot = updated
            self._change_token = self._registry_and_component_token()
            listeners = tuple(self._listeners.values())
        if updated.revision == previous.revision:
            return updated
        self.skill_hosts.refresh_contributed_sources()
        for listener in listeners:
            try:
                listener(updated)
            except Exception:
                logger.exception("Plugin change listener failed")
        return updated

    def subscribe(self, listener: PluginChangeListener) -> int:
        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            self._thread = None
            self._listeners.clear()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds * 2))
        self.skill_hosts.unregister_contributor(PLUGIN_SKILL_CONTRIBUTOR_ID)

    def poll_once(self) -> bool:
        """Inspect registry and fixed component state once for tests/embedders."""

        token = self._registry_and_component_token()
        with self._lock:
            changed = token != self._change_token
        if changed:
            self.refresh()
        return changed

    def _build_snapshot(self) -> PluginSnapshot:
        try:
            registrations = self.registry.list()
        except ValueError as exc:
            diagnostic = PluginDiagnostic(
                code="plugin.registry_invalid",
                severity=PluginDiagnosticSeverity.ERROR,
                message=str(exc)[:2_000],
            )
            digest = hashlib.sha256(diagnostic.message.encode()).hexdigest()
            return PluginSnapshot(
                (),
                f"sha256:{digest}",
                diagnostics=(diagnostic,),
            )

        results: list[PluginLoadResult] = []
        diagnostics: list[PluginDiagnostic] = []
        digest = hashlib.sha256()
        for registration in registrations:
            digest.update(registration.installation_id.encode())
            digest.update(registration.name.encode())
            digest.update(str(registration.source.path).encode())
            digest.update(b"1" if registration.enabled else b"0")
            result = self._resolve_registration(registration)
            results.append(result)
            diagnostics.extend(result.diagnostics)
            if result.plugin is not None:
                digest.update(result.plugin.package.manifest_revision.encode())
                for component in result.plugin.package.components:
                    digest.update(component.kind.value.encode())
                    digest.update(component.status.value.encode())
                    if component.revision is not None:
                        digest.update(component.revision.encode())
            if result.error is not None:
                digest.update(result.error.encode())
        return PluginSnapshot(
            tuple(results),
            f"sha256:{digest.hexdigest()}",
            diagnostics=tuple(diagnostics[:100]),
        )

    @staticmethod
    def _resolve_registration(
        registration: PluginRegistration,
    ) -> PluginLoadResult:
        try:
            plugin = resolve_plugin(registration.source.path)
            if plugin.name != registration.name:
                raise PluginValidationError(
                    f"Registered Plugin {registration.name!r} now declares "
                    f"name {plugin.name!r}"
                )
            diagnostics = [*plugin.package.diagnostics]
            diagnostics.extend(
                diagnostic
                for component in plugin.package.components
                for diagnostic in component.diagnostics
            )
            return PluginLoadResult(
                registration,
                plugin,
                diagnostics=tuple(diagnostics),
            )
        except (OSError, ValueError) as exc:
            return PluginLoadResult(
                registration,
                None,
                diagnostics=(
                    PluginDiagnostic(
                        code="plugin.load_failed",
                        severity=PluginDiagnosticSeverity.ERROR,
                        message=str(exc)[:2_000],
                    ),
                ),
            )

    def _registry_and_component_token(self) -> object:
        registry_token = self.registry.change_token()
        try:
            registrations = self.registry.list()
        except ValueError as exc:
            return (registry_token, type(exc).__name__, str(exc))
        fixed_resources = (
            PLUGIN_MANIFEST_RESOURCE,
            SKILLS_RESOURCE,
            MCP_RESOURCE,
        )
        packages = tuple(
            (
                registration.installation_id,
                tuple(
                    _stat_token(registration.source.path / resource.value)
                    for resource in fixed_resources
                ),
            )
            for registration in registrations
        )
        return registry_token, packages

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.poll_once()
            except Exception:
                logger.warning("Unable to refresh local Plugins", exc_info=True)


def _stat_token(path: Path) -> tuple[str, int | None, int | None]:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), None, None)


__all__ = [
    "PLUGIN_SKILL_CONTRIBUTOR_ID",
    "LocalPluginHost",
    "PluginChangeListener",
]
