"""Format-neutral domain models for inert Agent Plugin packages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

_INSTALLATION_ID_RE = re.compile(r"^plg_[0-9a-f]{24}$")


class PluginStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    INVALID = "invalid"


class PluginComponentKind(StrEnum):
    SKILLS = "skills"
    MCP = "mcp"


class PluginComponentStatus(StrEnum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


class PluginDiagnosticSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class PluginSourceKind(StrEnum):
    LINKED_DIRECTORY = "linked-directory"


class PluginError(ValueError):
    """Base class for deterministic Plugin failures."""


class PluginValidationError(PluginError):
    pass


class PluginRegistryError(PluginError):
    pass


class PluginAlreadyRegisteredError(PluginRegistryError):
    pass


class PluginRegistrationNotFoundError(PluginRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    code: str
    severity: PluginDiagnosticSeverity
    message: str
    component: PluginComponentKind | None = None
    resource: str | None = None


@dataclass(frozen=True, slots=True)
class PluginResourceReference:
    """Package-relative resource address resolved only by the package owner."""

    value: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.value)
        if (
            not self.value
            or "\\" in self.value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("Plugin resource must be a relative POSIX path")


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str | None = None
    description: str = ""
    author_name: str | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: tuple[str, ...] = ()
    extension_namespaces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginComponent:
    kind: PluginComponentKind
    status: PluginComponentStatus
    resource: PluginResourceReference | None = None
    item_count: int | None = None
    revision: str | None = None
    diagnostics: tuple[PluginDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginPackage:
    schema: str
    metadata: PluginMetadata
    components: tuple[PluginComponent, ...]
    manifest: PluginResourceReference
    manifest_revision: str
    diagnostics: tuple[PluginDiagnostic, ...] = ()

    def component(self, kind: PluginComponentKind) -> PluginComponent | None:
        return next((item for item in self.components if item.kind is kind), None)


@dataclass(frozen=True, slots=True)
class ResolvedPlugin:
    """Validated inert package bound to one filesystem root."""

    root: Path
    package: PluginPackage

    @property
    def name(self) -> str:
        return self.package.metadata.name

    @property
    def manifest_path(self) -> Path:
        return self.root / self.package.manifest.value


@dataclass(frozen=True, slots=True)
class PluginSource:
    kind: PluginSourceKind
    path: Path


@dataclass(frozen=True, slots=True)
class PluginRegistration:
    installation_id: str
    name: str
    source: PluginSource
    enabled: bool = True

    def __post_init__(self) -> None:
        if not _INSTALLATION_ID_RE.fullmatch(self.installation_id):
            raise ValueError("Invalid Plugin installation ID")


@dataclass(frozen=True, slots=True)
class PluginLoadResult:
    registration: PluginRegistration
    plugin: ResolvedPlugin | None
    diagnostics: tuple[PluginDiagnostic, ...] = ()

    @property
    def error(self) -> str | None:
        diagnostic = next(
            (
                item
                for item in self.diagnostics
                if item.severity is PluginDiagnosticSeverity.ERROR
                and item.component is None
            ),
            None,
        )
        return diagnostic.message if diagnostic is not None else None

    @property
    def status(self) -> PluginStatus:
        if self.plugin is None or self.error is not None:
            return PluginStatus.INVALID
        return (
            PluginStatus.ACTIVE if self.registration.enabled else PluginStatus.DISABLED
        )


@dataclass(frozen=True, slots=True)
class PluginSnapshot:
    plugins: tuple[PluginLoadResult, ...]
    revision: str
    diagnostics: tuple[PluginDiagnostic, ...] = ()


__all__ = [
    "PluginAlreadyRegisteredError",
    "PluginComponent",
    "PluginComponentKind",
    "PluginComponentStatus",
    "PluginDiagnostic",
    "PluginDiagnosticSeverity",
    "PluginError",
    "PluginLoadResult",
    "PluginMetadata",
    "PluginPackage",
    "PluginRegistration",
    "PluginRegistrationNotFoundError",
    "PluginRegistryError",
    "PluginResourceReference",
    "PluginSnapshot",
    "PluginSource",
    "PluginSourceKind",
    "PluginStatus",
    "PluginValidationError",
    "ResolvedPlugin",
]
