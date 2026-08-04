"""Application adapter for the frontend-neutral local Skill manager."""

from __future__ import annotations

from dataclasses import dataclass

from core.application.errors import (
    ConflictError,
    InvalidArgumentError,
    NotSupportedApplicationError,
    ProjectNotTrustedError,
    SkillNotFoundError,
)
from core.application.project_service import ProjectService
from core.domain.project import TrustState
from core.skills.management import LocalSkillManager
from core.skills.models import (
    SkillRecord,
    SkillResolutionError,
    SkillScope,
    SkillSourceRoot,
    SkillStatus,
    SkillValidationError,
)

MAX_SKILL_INSTRUCTIONS = 64 * 1024


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


class SkillService:
    """Product-safe Skill operations scoped through a registered Project."""

    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

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
        instructions = record.instructions[:MAX_SKILL_INSTRUCTIONS]
        return SkillDetail(
            info=_skill_info(record),
            instructions=instructions,
            truncated=len(record.instructions) > MAX_SKILL_INSTRUCTIONS,
        )

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
            return LocalSkillManager(project.canonical_path)
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(
                f"project path does not exist: {project.canonical_path}"
            ) from exc


def _discovery(catalog) -> SkillDiscovery:
    return SkillDiscovery(
        skills=tuple(_skill_info(record) for record in catalog.records),
        warnings=catalog.warnings,
        catalog_revision=catalog.revision,
    )


def _skill_info(record: SkillRecord) -> SkillInfo:
    root = {
        SkillSourceRoot.AGENTS: ".agents",
        SkillSourceRoot.DEEPCODE: ".deepcode",
        SkillSourceRoot.CLAUDE: ".claude",
        SkillSourceRoot.SYSTEM: "bundled",
    }[record.source_root]
    return SkillInfo(
        id=record.id,
        name=record.name,
        description=record.description,
        allowed_tools=record.allowed_tools,
        scope=record.scope.value,
        source_root=record.source_root.value,
        source=record.source,
        location=f"{record.scope.value}/{root}/skills/{record.key.relative_path}",
        status=record.status.value,
        enabled=record.status is not SkillStatus.DISABLED,
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
        deletable=record.deletable,
    )


__all__ = [
    "SkillDetail",
    "SkillDiscovery",
    "SkillInfo",
    "SkillService",
]
