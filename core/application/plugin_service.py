"""Frontend-neutral management of the local Agent Plugin registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    PluginNotFoundError,
)
from core.plugins.domain import (
    PluginAlreadyRegisteredError,
    PluginComponent,
    PluginDiagnostic,
    PluginLoadResult,
    PluginRegistrationNotFoundError,
    PluginSnapshot,
)
from core.plugins.host import LocalPluginHost
from core.plugins.resolver import resolve_plugin


@dataclass(frozen=True, slots=True)
class PluginDiagnosticInfo:
    code: str
    severity: str
    message: str
    component: str | None
    resource: str | None


@dataclass(frozen=True, slots=True)
class PluginComponentInfo:
    kind: str
    status: str
    resource: str | None
    item_count: int | None
    diagnostics: tuple[PluginDiagnosticInfo, ...]


@dataclass(frozen=True, slots=True)
class PluginInfo:
    id: str
    name: str
    version: str | None
    description: str
    status: str
    enabled: bool
    source: str
    path: str
    schema: str | None
    manifest_path: str
    manifest_revision: str | None
    components: tuple[PluginComponentInfo, ...]
    diagnostics: tuple[PluginDiagnosticInfo, ...]
    error: str | None


@dataclass(frozen=True, slots=True)
class PluginDiscovery:
    plugins: tuple[PluginInfo, ...]
    diagnostics: tuple[PluginDiagnosticInfo, ...]
    revision: str


class PluginService:
    def __init__(self, host: LocalPluginHost) -> None:
        self.host = host

    def list(self) -> PluginDiscovery:
        return _discovery(self.host.snapshot())

    def add(self, path: str) -> PluginDiscovery:
        try:
            plugin = resolve_plugin(path)
            self.host.registry.add(plugin)
            return _discovery(self.host.refresh())
        except PluginAlreadyRegisteredError as exc:
            raise ConflictError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

    def set_enabled(self, plugin_id: str, *, enabled: bool) -> PluginDiscovery:
        try:
            self.host.registry.set_enabled(plugin_id, enabled)
            return _discovery(self.host.refresh())
        except PluginRegistrationNotFoundError as exc:
            raise PluginNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

    def remove(self, plugin_id: str) -> PluginInfo:
        current = next(
            (
                plugin
                for plugin in self.list().plugins
                if plugin.id == plugin_id or plugin.name == plugin_id
            ),
            None,
        )
        if current is None:
            raise PluginNotFoundError(f"Plugin is not registered: {plugin_id}")
        try:
            self.host.registry.remove(current.id)
            self.host.refresh()
        except PluginRegistrationNotFoundError as exc:
            raise PluginNotFoundError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return current

    def subscribe_changes(self, listener: Callable[[PluginDiscovery], None]) -> int:
        return self.host.subscribe(lambda snapshot: listener(_discovery(snapshot)))

    def unsubscribe_changes(self, token: int) -> None:
        self.host.unsubscribe(token)

    def close(self) -> None:
        self.host.close()


def _discovery(snapshot: PluginSnapshot) -> PluginDiscovery:
    return PluginDiscovery(
        plugins=tuple(_plugin_info(result) for result in snapshot.plugins),
        diagnostics=tuple(_diagnostic_info(item) for item in snapshot.diagnostics),
        revision=snapshot.revision,
    )


def _plugin_info(result: PluginLoadResult) -> PluginInfo:
    registration = result.registration
    plugin = result.plugin
    metadata = plugin.package.metadata if plugin is not None else None
    return PluginInfo(
        id=registration.installation_id,
        name=metadata.name if metadata is not None else registration.name,
        version=metadata.version if metadata is not None else None,
        description=metadata.description if metadata is not None else "",
        status=result.status.value,
        enabled=registration.enabled,
        source=registration.source.kind.value,
        path=str(registration.source.path),
        schema=plugin.package.schema if plugin is not None else None,
        manifest_path=str(
            plugin.manifest_path
            if plugin is not None
            else registration.source.path / "plugin.json"
        ),
        manifest_revision=(
            plugin.package.manifest_revision if plugin is not None else None
        ),
        components=(
            tuple(_component_info(component) for component in plugin.package.components)
            if plugin is not None
            else ()
        ),
        diagnostics=tuple(_diagnostic_info(item) for item in result.diagnostics),
        error=result.error,
    )


def _component_info(component: PluginComponent) -> PluginComponentInfo:
    return PluginComponentInfo(
        kind=component.kind.value,
        status=component.status.value,
        resource=component.resource.value if component.resource is not None else None,
        item_count=component.item_count,
        diagnostics=tuple(_diagnostic_info(item) for item in component.diagnostics),
    )


def _diagnostic_info(diagnostic: PluginDiagnostic) -> PluginDiagnosticInfo:
    return PluginDiagnosticInfo(
        code=diagnostic.code,
        severity=diagnostic.severity.value,
        message=diagnostic.message,
        component=(
            diagnostic.component.value if diagnostic.component is not None else None
        ),
        resource=diagnostic.resource,
    )


__all__ = [
    "PluginComponentInfo",
    "PluginDiagnosticInfo",
    "PluginDiscovery",
    "PluginInfo",
    "PluginService",
]
