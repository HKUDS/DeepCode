"""Stable application errors shared by CLI and App Server adapters."""

from __future__ import annotations

from enum import StrEnum
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


class UpgradeRequiresExclusiveAccessError(ApplicationError):
    """The database cannot be migrated while another application is live."""

    code = "UPGRADE_REQUIRES_EXCLUSIVE_ACCESS"
    retryable = True

    def __init__(self, installed_version: int, required_version: int) -> None:
        super().__init__(
            "database schema upgrade requires exclusive application access "
            f"(installed v{installed_version}, required v{required_version})",
            user_message=(
                "DeepCode needs to upgrade its local database. Close other "
                "DeepCode CLI/Desktop processes, then try again."
            ),
            details={
                "installedSchemaVersion": installed_version,
                "requiredSchemaVersion": required_version,
            },
        )


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


class AutomationBootstrapPendingError(ApplicationError):
    """A durable Automation exists but its canonical Session needs repair."""

    code = "AUTOMATION_BOOTSTRAP_PENDING"
    retryable = False

    def __init__(self, automation_id: str, thread_id: str) -> None:
        super().__init__(
            "automation was durably created but Session materialization failed",
            user_message=(
                "The Automation was durably created, but its Session is not "
                "ready yet. Refresh or reopen DeepCode; do not retry Create."
            ),
            details={
                "automationId": automation_id,
                "threadId": thread_id,
                "accepted": True,
                "recovery": "refresh_or_reopen",
            },
        )


class GoalNotFoundError(ApplicationError):
    code = "GOAL_NOT_FOUND"


class SkillNotFoundError(ApplicationError):
    code = "SKILL_NOT_FOUND"


class PluginNotFoundError(ApplicationError):
    code = "PLUGIN_NOT_FOUND"


class WorkflowInteractionError(ApplicationError):
    code = "WORKFLOW_INTERACTION_INVALID"


class ApprovalAlreadyResolvedError(ApplicationError):
    code = "APPROVAL_EXPIRED"


class ProjectNotTrustedError(ApplicationError):
    code = "PERMISSION_DENIED"


class TurnAlreadyRunningError(ApplicationError):
    """Compatibility name for the strict active-Turn conflict."""

    code = "TURN_ALREADY_ACTIVE"


# New code should use the product term "active". Keep the historical class
# name as an alias so older Python integrations do not break on import.
TurnAlreadyActiveError = TurnAlreadyRunningError


class NoActiveTurnError(ApplicationError):
    code = "NO_ACTIVE_TURN"
    retryable = True


class ExpectedTurnMismatchError(ApplicationError):
    code = "EXPECTED_TURN_MISMATCH"
    retryable = True

    def __init__(
        self,
        expected_turn_id: str,
        actual_turn_id: str | None,
    ) -> None:
        actual = actual_turn_id or "none"
        super().__init__(
            f"expected active Turn {expected_turn_id}, actual active Turn is {actual}",
            details={
                "expectedTurnId": expected_turn_id,
                "actualTurnId": actual_turn_id,
            },
        )


class TurnInputBoundaryState(StrEnum):
    """Stable application representation of the live input boundary."""

    STARTING = "starting"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"

    @property
    def is_finalizing(self) -> bool:
        return self in {self.CLOSING, self.CLOSED}


class TurnNotSteerableError(ApplicationError):
    code = "TURN_NOT_STEERABLE"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        state: TurnInputBoundaryState | None = None,
        user_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        clean_details = dict(details or {})
        if state is not None:
            clean_details["state"] = state.value
        super().__init__(
            message,
            user_message=user_message,
            details=clean_details,
        )

    @property
    def boundary_state(self) -> TurnInputBoundaryState | None:
        value = self.details.get("state")
        try:
            return TurnInputBoundaryState(value)
        except (TypeError, ValueError):
            return None

    @property
    def crossed_final_input_boundary(self) -> bool:
        state = self.boundary_state
        return state is not None and state.is_finalizing


class EmptyInputError(ApplicationError):
    code = "EMPTY_INPUT"


class InputTooLargeError(ApplicationError):
    code = "INPUT_TOO_LARGE"


class TurnInputCapacityExceededError(ApplicationError):
    code = "TURN_INPUT_CAPACITY"
    retryable = True


class DuplicateMessageConflictError(ApplicationError):
    code = "DUPLICATE_MESSAGE_CONFLICT"


class TurnInterruptTimeoutError(ApplicationError):
    code = "TURN_INTERRUPT_TIMEOUT"
    retryable = True


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
