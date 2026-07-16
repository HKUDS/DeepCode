"""Read-only discovery views for workspace Skills and lifecycle Hooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.application.errors import InvalidArgumentError
from core.application.project_service import ProjectService
from core.harness.hooks import discover_hooks
from core.harness.skills import Skill, discover_skills


MAX_SKILL_INSTRUCTIONS = 64 * 1024
MAX_HOOK_HANDLERS = 500
MAX_DISCOVERY_WARNINGS = 100


@dataclass(frozen=True, slots=True)
class SkillInfo:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    directory: str
    source: str


@dataclass(frozen=True, slots=True)
class SkillDetail:
    info: SkillInfo
    instructions: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class HookInfo:
    event_name: str
    matcher: str | None
    command: str
    timeout_seconds: int
    source: str
    source_path: str
    display_order: int
    status_message: str | None


@dataclass(frozen=True, slots=True)
class SkillDiscovery:
    skills: tuple[SkillInfo, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HookDiscovery:
    hooks: tuple[HookInfo, ...]
    warnings: tuple[str, ...]
    truncated: bool


class ExtensionService:
    """Expose the exact discovery inputs used by Agent Sessions."""

    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def skills(self, project_id: str) -> SkillDiscovery:
        root = self._project_root(project_id)
        registry = discover_skills(root)
        return SkillDiscovery(
            skills=tuple(_skill_info(skill) for skill in registry.all()),
            warnings=_bounded_warnings(registry.errors),
        )

    def skill(self, project_id: str, name: str) -> SkillDetail:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidArgumentError("skill name must not be empty")
        root = self._project_root(project_id)
        registry = discover_skills(root)
        skill = registry.get(clean_name)
        if skill is None:
            raise InvalidArgumentError(f"skill is not available: {clean_name}")
        instructions = skill.instructions[:MAX_SKILL_INSTRUCTIONS]
        return SkillDetail(
            info=_skill_info(skill),
            instructions=instructions,
            truncated=len(skill.instructions) > MAX_SKILL_INSTRUCTIONS,
        )

    def hooks(self, project_id: str) -> HookDiscovery:
        root = self._project_root(project_id)
        discovery = discover_hooks(str(root))
        visible = discovery.handlers[:MAX_HOOK_HANDLERS]
        return HookDiscovery(
            hooks=tuple(
                HookInfo(
                    event_name=handler.event_name,
                    matcher=handler.matcher,
                    command=handler.command,
                    timeout_seconds=handler.timeout_sec,
                    source=handler.source,
                    source_path=handler.source_path,
                    display_order=handler.display_order,
                    status_message=handler.status_message,
                )
                for handler in visible
            ),
            warnings=_bounded_warnings(discovery.warnings),
            truncated=len(discovery.handlers) > len(visible),
        )

    def _project_root(self, project_id: str) -> Path:
        project = self.projects.read(project_id)
        try:
            root = Path(project.canonical_path).resolve(strict=True)
        except OSError as exc:
            raise InvalidArgumentError(
                f"project path does not exist: {project.canonical_path}"
            ) from exc
        if not root.is_dir():
            raise InvalidArgumentError("project path must be a directory")
        return root


def _skill_info(skill: Skill) -> SkillInfo:
    return SkillInfo(
        name=skill.name,
        description=skill.description,
        allowed_tools=skill.allowed_tools,
        directory=skill.directory,
        source=skill.source,
    )


def _bounded_warnings(values: list[str]) -> tuple[str, ...]:
    return tuple(str(value)[:2_000] for value in values[:MAX_DISCOVERY_WARNINGS])
