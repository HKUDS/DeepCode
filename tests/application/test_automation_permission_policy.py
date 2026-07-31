from __future__ import annotations

import pytest

from core.application.automation_permission_policy import (
    WorkspaceAutomationPermissionPolicy,
)
from core.config import DeepCodeConfig
from core.domain.execution_permission import ExecutionPermissionMode


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ({}, ExecutionPermissionMode.DEFAULT),
        (
            {"security": {"permissionMode": "plan"}},
            ExecutionPermissionMode.PLAN,
        ),
        (
            {"security": {"permission_mode": "full_auto"}},
            ExecutionPermissionMode.FULL_AUTO,
        ),
        (
            {"security": {"permissionMode": "invalid"}},
            ExecutionPermissionMode.DEFAULT,
        ),
    ),
)
def test_workspace_policy_uses_explicit_config_or_safe_default(
    raw: dict,
    expected: ExecutionPermissionMode,
) -> None:
    config = DeepCodeConfig.model_validate(raw)
    policy = WorkspaceAutomationPermissionPolicy(
        config_loader=lambda _workspace: config,
    )

    assert policy.resolve("/workspace") is expected


def test_workspace_policy_does_not_inherit_host_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "full_auto")
    config = DeepCodeConfig.model_validate({})
    policy = WorkspaceAutomationPermissionPolicy(
        config_loader=lambda _workspace: config,
    )

    assert policy.resolve("/workspace") is ExecutionPermissionMode.DEFAULT
