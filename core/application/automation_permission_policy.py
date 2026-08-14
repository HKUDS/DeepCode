"""Host-neutral permission policy for automatic Turn execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from core.config import DeepCodeConfig, load_config_for_workspace
from core.domain.execution_permission import ExecutionPermissionMode


class AutomationPermissionPolicy(Protocol):
    """Resolve the permission snapshot for a workspace Automation Turn."""

    def resolve(self, workspace: str) -> ExecutionPermissionMode:
        """Return one explicit, durable execution mode."""


@dataclass(frozen=True, slots=True)
class WorkspaceAutomationPermissionPolicy:
    """Use explicit layered workspace configuration or a safe default.

    This resolver intentionally ignores the host surface and does not inspect
    Automation instructions. Resolution happens once at Turn admission and the
    typed result is persisted, so later workers consume the same decision.
    """

    config_loader: Callable[[str], DeepCodeConfig] = load_config_for_workspace
    default_mode: ExecutionPermissionMode = ExecutionPermissionMode.DEFAULT

    def resolve(self, workspace: str) -> ExecutionPermissionMode:
        security = getattr(self.config_loader(workspace), "security", None)
        raw = _explicit_permission_mode(security)
        if raw is None:
            return self.default_mode
        try:
            return ExecutionPermissionMode(raw.strip().lower())
        except ValueError:
            return self.default_mode


def _explicit_permission_mode(security: Any | None) -> str | None:
    if security is None:
        return None
    fields_set = getattr(security, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(security, "__fields_set__", None)
    if fields_set is not None and "permission_mode" not in fields_set:
        return None
    value = getattr(security, "permission_mode", None)
    return str(value) if value is not None else None


__all__ = [
    "AutomationPermissionPolicy",
    "WorkspaceAutomationPermissionPolicy",
]
