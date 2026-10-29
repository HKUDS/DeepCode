"""Read-only lifecycle Hook views plus legacy Skill-service compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.application.errors import InvalidArgumentError
from core.application.project_service import ProjectService, approved_hooks_digest
from core.domain.project import TrustState
from core.harness.hooks import discover_hooks
from core.application.skill_service import (
    SkillDetail,
    SkillDiscovery,
    SkillService,
)


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
class ProjectHooksApproval:
    """Whether the project's own hooks will run on the next Turn.

    ``status`` is ``none`` when the project declares no runnable hooks,
    ``approved`` when ``digest`` is the approved one, else ``unapproved``.
    """

    digest: str
    approved_digest: str | None
    status: str


@dataclass(frozen=True, slots=True)
class HookDiscovery:
    hooks: tuple[HookInfo, ...]
    warnings: tuple[str, ...]
    truncated: bool
    project_hooks: ProjectHooksApproval


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
        """Every hook the project's sessions would discover, project hooks
        included whether or not they are approved, plus their approval."""
        project = self.projects.read(project_id)
        root = self._project_root(project_id)
        discovery = discover_hooks(str(root))
        visible = discovery.handlers[:MAX_HOOK_HANDLERS]
        digest = discovery.project_digest
        approved = approved_hooks_digest(project)
        if approved is None and project.trust_state is TrustState.TRUSTED:
            # The next Turn adopts these (ProjectService.hook_approval).
            approved = digest
        if not any(handler.source == "project" for handler in discovery.handlers):
            status = "none"
        elif approved == digest:
            status = "approved"
        else:
            status = "unapproved"
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
            project_hooks=ProjectHooksApproval(
                digest=digest,
                approved_digest=approved,
                status=status,
            ),
        )

    def approve_hooks(self, project_id: str, digest: str) -> HookDiscovery:
        """Approve the project hooks listed under ``digest``; list them again."""
        self._project_root(project_id)
        self.projects.approve_hooks(project_id, digest)
        return self.hooks(project_id)

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
