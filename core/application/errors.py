"""Stable application errors shared by CLI and App Server adapters."""

from __future__ import annotations

from typing import Any


class ApplicationError(RuntimeError):
    code = "INTERNAL_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.details = details or {}


class InvalidArgumentError(ApplicationError):
    code = "INVALID_REQUEST"


class ProjectNotFoundError(ApplicationError):
    code = "PROJECT_NOT_FOUND"


class ThreadNotFoundError(ApplicationError):
    code = "THREAD_NOT_FOUND"


class TurnNotFoundError(ApplicationError):
    code = "TURN_NOT_FOUND"


class ApprovalNotFoundError(ApplicationError):
    code = "APPROVAL_NOT_FOUND"


class WorkflowNotFoundError(ApplicationError):
    code = "WORKFLOW_NOT_FOUND"


class ArtifactNotFoundError(ApplicationError):
    code = "ARTIFACT_NOT_FOUND"


class AutomationNotFoundError(ApplicationError):
    code = "AUTOMATION_NOT_FOUND"


class GoalNotFoundError(ApplicationError):
    code = "GOAL_NOT_FOUND"


class SkillNotFoundError(ApplicationError):
    code = "SKILL_NOT_FOUND"


class WorkflowInteractionError(ApplicationError):
    code = "WORKFLOW_INTERACTION_INVALID"


class ApprovalAlreadyResolvedError(ApplicationError):
    code = "APPROVAL_EXPIRED"


class ProjectNotTrustedError(ApplicationError):
    code = "PERMISSION_DENIED"


class TurnAlreadyRunningError(ApplicationError):
    code = "TURN_ALREADY_RUNNING"


class ConflictError(ApplicationError):
    code = "CONFLICT"


class WorkspaceOutOfScopeError(ApplicationError):
    code = "WORKSPACE_OUT_OF_SCOPE"


class FileNotFoundApplicationError(ApplicationError):
    code = "FILE_NOT_FOUND"


class FileChangedError(ApplicationError):
    code = "FILE_CHANGED"


class FileTooLargeError(ApplicationError):
    code = "FILE_TOO_LARGE"


class BinaryFileError(ApplicationError):
    code = "BINARY_FILE"


class GitUnavailableError(ApplicationError):
    code = "GIT_UNAVAILABLE"


class TerminalNotFoundError(ApplicationError):
    code = "TERMINAL_NOT_FOUND"


class NotSupportedApplicationError(ApplicationError):
    code = "NOT_SUPPORTED"
