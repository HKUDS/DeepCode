"""Application adapter for the frontend-neutral local Skill manager."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    NotSupportedApplicationError,
    ProjectNotTrustedError,
    SkillNotFoundError,
)
from core.application.project_service import ProjectService
from core.domain.project import TrustState
from core.skills.builtin_registry import BuiltinSkillRole, find_builtin_skill
from core.skills.host import SkillWorkspaceRegistry
from core.skills.management import LocalSkillManager
from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    MUTABLE_SKILL_SCOPES,
    SkillRecord,
    SkillResolutionError,
    SkillScope,
    SkillValidationError,
)

MAX_SKILL_INSTRUCTIONS = 64 * 1024
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillInfo:
    id: str
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    scope: str
    source_root: str
    source: str
    location: str
    origin_kind: str
    origin_label: str
    provider_kind: str
    provider_id: str
    package_id: str
    status: str
    enabled: bool
    selectable: bool
    revision: str
    byte_size: int
    shadowed_by: str | None
    error: str | None
    display_name: str | None
    short_description: str | None
    icon_small: str | None
    icon_large: str | None
    brand_color: str | None
    default_prompt: str | None
    allow_implicit_invocation: bool
    configurable_scopes: tuple[str, ...]
    deletable: bool


@dataclass(frozen=True, slots=True)
class SkillDetail:
    info: SkillInfo
    instructions: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class SkillDiscovery:
    skills: tuple[SkillInfo, ...]
    warnings: tuple[str, ...]
    catalog_revision: str
    authoring_skill_id: str | None


class SkillService:
    """Product-safe Skill operations scoped through a registered Project."""

    def __init__(
        self,
        projects: ProjectService,
        hosts: SkillWorkspaceRegistry | None = None,
    ) -> None:
        self.projects = projects
        self._owns_hosts = hosts is None
        self.hosts = hosts or SkillWorkspaceRegistry()
        self._project_ids_by_workspace: dict[Path, set[str]] = {}
        self._listeners: dict[int, Callable[[str], None]] = {}
        self._next_listener = 1
        self._lock = threading.RLock()
        self._host_subscription: int | None = self.hosts.subscribe(
            self._catalog_changed
        )

    def close(self) -> None:
        with self._lock:
            subscription = self._host_subscription
            self._host_subscription = None
            self._project_ids_by_workspace.clear()
            self._listeners.clear()
        if subscription is not None:
            self.hosts.unsubscribe(subscription)
        if self._owns_hosts:
            self.hosts.close()

    def subscribe_changes(self, listener: Callable[[str], None]) -> int:
        """Subscribe to catalog invalidations for discovered projects."""

        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            return token

    def unsubscribe_changes(self, token: int) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def list(self, project_id: str, *, force: bool = False) -> SkillDiscovery:
        try:
            catalog = self._manager(project_id).catalog(force=force)
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return _discovery(catalog)

    def read(self, project_id: str, identifier: str) -> SkillDetail:
        manager = self._manager(project_id)
        try:
            record = manager.find(identifier)
        except SkillResolutionError as exc:
            raise SkillNotFoundError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if record.scope is SkillScope.PROJECT:
            project = self.projects.read(project_id)
            if project.trust_state is not TrustState.TRUSTED:
                raise ProjectNotTrustedError(
                    "project must be trusted before project Skill instructions "
                    "can be read"
                )
        full_instructions = record.require_instructions()
        instructions = full_instructions[:MAX_SKILL_INSTRUCTIONS]
        return SkillDetail(
            info=_skill_info(record),
            instructions=instructions,
            truncated=len(full_instructions) > MAX_SKILL_INSTRUCTIONS,
        )

    def select(self, project_id: str, identifier: str) -> SkillInfo:
        """Resolve an explicit selection without loading its instruction body."""

        try:
            record = self._manager(project_id).select(identifier)
        except SkillResolutionError as exc:
            raise SkillNotFoundError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return _skill_info(record)

    def set_enabled(
        self,
        project_id: str,
        skill_id: str,
        *,
        enabled: bool,
        scope: SkillScope,
    ) -> SkillDiscovery:
        manager = self._manager(project_id, require_trust=scope is SkillScope.PROJECT)
        try:
            catalog = manager.set_enabled(
                skill_id,
                enabled=enabled,
                config_scope=scope,
            )
        except (SkillResolutionError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return _discovery(catalog)

    def import_directory(
        self,
        project_id: str,
        source: str,
        *,
        scope: SkillScope,
    ) -> SkillDetail:
        manager = self._manager(project_id, require_trust=scope is SkillScope.PROJECT)
        try:
            record = manager.import_directory(source, target_scope=scope)
        except FileExistsError as exc:
            raise ConflictError(str(exc)) from exc
        except (OSError, SkillValidationError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return self.read(project_id, record.id)

    def delete(self, project_id: str, skill_id: str) -> bool:
        manager = self._manager(project_id)
        try:
            record = manager.find(skill_id)
        except SkillResolutionError as exc:
            raise SkillNotFoundError(str(exc)) from exc
        if record.scope is SkillScope.PROJECT:
            manager = self._manager(project_id, require_trust=True)
        try:
            manager.delete(skill_id)
        except PermissionError as exc:
            raise NotSupportedApplicationError(str(exc)) from exc
        except SkillResolutionError as exc:
            raise SkillNotFoundError(str(exc)) from exc
        except (OSError, SkillValidationError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        return True

    def _manager(
        self,
        project_id: str,
        *,
        require_trust: bool = False,
    ) -> LocalSkillManager:
        project = self.projects.read(project_id)
        if require_trust and project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before project Skill changes"
            )
        try:
            workspace = Path(project.canonical_path).resolve(strict=True)
            host = self.hosts.get(workspace)
            with self._lock:
                self._project_ids_by_workspace.setdefault(workspace, set()).add(
                    project.id
                )
            return LocalSkillManager(host=host)
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(
                f"project path does not exist: {project.canonical_path}"
            ) from exc

    def _catalog_changed(self, workspace: Path) -> None:
        with self._lock:
            project_ids = tuple(self._project_ids_by_workspace.get(workspace, ()))
            listeners = tuple(self._listeners.values())
        for project_id in project_ids:
            for listener in listeners:
                try:
                    listener(project_id)
                except Exception:  # noqa: BLE001 - observers are isolated
                    logger.exception("Skill catalog change listener failed")


def _discovery(catalog) -> SkillDiscovery:
    authoring = find_builtin_skill(catalog.records, BuiltinSkillRole.AUTHORING)
    return SkillDiscovery(
        skills=tuple(_skill_info(record) for record in catalog.records),
        warnings=catalog.warnings,
        catalog_revision=catalog.revision,
        authoring_skill_id=authoring.id if authoring is not None else None,
    )


def _skill_info(record: SkillRecord) -> SkillInfo:
    origin = record.origin
    if origin is None:  # Defensive guard for non-dataclass deserializers.
        raise RuntimeError("Skill record has no origin")
    authority = record.authority
    return SkillInfo(
        id=record.id,
        name=record.name,
        description=record.description,
        allowed_tools=record.allowed_tools,
        scope=record.scope.value,
        source_root=record.source_root.value,
        source=record.source,
        location=origin.location,
        origin_kind=origin.kind.value,
        origin_label=origin.label,
        provider_kind=authority.kind.value,
        provider_id=authority.provider_id,
        package_id=record.package_id.value,
        status=record.status.value,
        enabled=record.policy_enabled,
        selectable=record.selectable,
        revision=record.revision,
        byte_size=record.byte_size,
        shadowed_by=record.shadowed_by,
        error=record.error,
        display_name=record.metadata.interface.display_name,
        short_description=record.metadata.interface.short_description,
        icon_small=record.metadata.interface.icon_small,
        icon_large=record.metadata.interface.icon_large,
        brand_color=record.metadata.interface.brand_color,
        default_prompt=record.metadata.interface.default_prompt,
        allow_implicit_invocation=record.metadata.allow_implicit_invocation,
        configurable_scopes=(
            tuple(scope.value for scope in MUTABLE_SKILL_SCOPES)
            if authority == LOCAL_SKILL_AUTHORITY
            else ()
        ),
        deletable=record.deletable,
    )


__all__ = [
    "SkillDetail",
    "SkillDiscovery",
    "SkillInfo",
    "SkillService",
]
