"""Shared CLI adapter for DeepCode's persistent workspace trust boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.application.errors import ProjectNotTrustedError
from core.domain.project import Project, TrustState

if TYPE_CHECKING:
    from core.application.application import DeepCodeApplication


def open_workspace_project(
    application: DeepCodeApplication,
    workspace: str,
    *,
    grant_trust: bool,
) -> Project:
    """Open one canonical Project and optionally persist an explicit grant."""

    project = application.projects.add(workspace)
    return set_project_trusted(application, project) if grant_trust else project


def set_project_trusted(
    application: DeepCodeApplication,
    project: Project,
) -> Project:
    if project.trust_state is TrustState.TRUSTED:
        return project
    return application.projects.update(project.id, trust_state=TrustState.TRUSTED)


def require_project_trusted(project: Project) -> None:
    if project.trust_state is TrustState.TRUSTED:
        return
    raise ProjectNotTrustedError(
        "project must be trusted before agent execution",
        user_message=(
            "This workspace is not trusted yet. Review the folder, then rerun "
            "with --trust. Trust is remembered for this canonical path and is "
            "separate from the Session's tool-access preset."
        ),
        details={"projectId": project.id, "path": project.canonical_path},
    )


__all__ = [
    "open_workspace_project",
    "require_project_trusted",
    "set_project_trusted",
]
