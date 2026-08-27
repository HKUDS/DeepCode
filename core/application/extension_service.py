"""Read-only lifecycle Hook views plus legacy Skill-service compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.application.errors import InvalidArgumentError
from core.application.project_service import ProjectService
from core.application.skill_service import (
    SkillDetail,
    SkillDiscovery,
    SkillService,
)
from core.harness.hooks import discover_hooks

MAX_HOOK_HANDLERS = 500
MAX_DISCOVERY_WARNINGS = 100


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
class HookDiscovery:
    hooks: tuple[HookInfo, ...]
    warnings: tuple[str, ...]
    truncated: bool


class ExtensionService:
    """Expose the exact discovery inputs used by Agent Sessions."""

    def __init__(self, projects: ProjectService, skills: SkillService) -> None:
        self.projects = projects
        self._skills = skills

    def skills(self, project_id: str) -> SkillDiscovery:
        return self._skills.list(project_id)

    def skill(self, project_id: str, identifier: str) -> SkillDetail:
        return self._skills.read(project_id, identifier)

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


def _bounded_warnings(values: list[str]) -> tuple[str, ...]:
    return tuple(str(value)[:2_000] for value in values[:MAX_DISCOVERY_WARNINGS])
