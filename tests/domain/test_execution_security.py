from __future__ import annotations

import pytest

from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionAccessPreset,
    ExecutionPermissionRuleAction,
    ExecutionPermissionRuleSnapshot,
    ExecutionSecurityProfile,
    FilesystemScope,
    normalize_permission_rules,
    parse_access_preset_override,
)


@pytest.mark.parametrize(
    ("preset", "permission_mode", "command_sandbox", "scope", "approvals"),
    [
        (
            ExecutionAccessPreset.ASK,
            ExecutionPermissionMode.DEFAULT,
            True,
            FilesystemScope.WORKSPACE,
            ApprovalPolicy.ON_REQUEST,
        ),
        (
            ExecutionAccessPreset.READ_ONLY,
            ExecutionPermissionMode.PLAN,
            True,
            FilesystemScope.WORKSPACE,
            ApprovalPolicy.NEVER,
        ),
        (
            ExecutionAccessPreset.FULL_ACCESS,
            ExecutionPermissionMode.FULL_AUTO,
            False,
            FilesystemScope.UNRESTRICTED,
            ApprovalPolicy.NEVER,
        ),
        # 危险逃生通道 (借鉴 Claude Code --allow-dangerously-skip-permissions):
        # 与 FULL_ACCESS 同强度, 但名字显式带"危险"警示, 供日志/UI 区分。
        (
            ExecutionAccessPreset.DANGEROUS_SKIP,
            ExecutionPermissionMode.FULL_AUTO,
            False,
            FilesystemScope.UNRESTRICTED,
            ApprovalPolicy.NEVER,
        ),
    ],
)
def test_access_presets_resolve_to_canonical_security_facts(
    preset: ExecutionAccessPreset,
    permission_mode: ExecutionPermissionMode,
    command_sandbox: bool,
    scope: FilesystemScope,
    approvals: ApprovalPolicy,
) -> None:
    profile = ExecutionSecurityProfile.for_preset(preset)

    assert profile.access_preset is preset
    assert profile.permission_mode is permission_mode
    assert profile.command_sandbox is command_sandbox
    assert profile.filesystem_scope is scope
    assert profile.approval_policy is approvals
    assert ExecutionSecurityProfile.from_dict(profile.to_dict()) == profile


def test_legacy_profile_preserves_facts_without_claiming_full_access() -> None:
    profile = ExecutionSecurityProfile.from_legacy(
        ExecutionPermissionMode.FULL_AUTO,
        command_sandbox=True,
    )

    assert profile.access_preset is None
    assert profile.permission_mode is ExecutionPermissionMode.FULL_AUTO
    assert profile.command_sandbox is True
    assert ExecutionSecurityProfile.from_dict(profile.to_dict()) == profile


def test_dangerous_skip_is_named_escape_hatch_not_masked_full_access() -> None:
    dangerous = ExecutionSecurityProfile.for_preset(
        ExecutionAccessPreset.DANGEROUS_SKIP
    )
    full = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)

    # 同强度: 解析到与 FULL_ACCESS 相同的 security facts
    assert dangerous.permission_mode is full.permission_mode
    assert dangerous.command_sandbox is full.command_sandbox
    assert dangerous.filesystem_scope is full.filesystem_scope
    assert dangerous.approval_policy is full.approval_policy

    # 但名字显式带"危险": 序列化 roundtrip 保留 dangerous_skip, 不冒充 full_access
    assert dangerous.access_preset is ExecutionAccessPreset.DANGEROUS_SKIP
    assert dangerous.to_dict()["accessPreset"] == "dangerous_skip"
    assert (
        ExecutionSecurityProfile.from_dict(dangerous.to_dict()).access_preset
        is ExecutionAccessPreset.DANGEROUS_SKIP
    )


def test_session_access_override_parser_is_canonical_and_fail_closed() -> None:
    assert (
        parse_access_preset_override(
            {
                "access_preset_override": None,
                "accessPresetOverride": "full_access",
            }
        )
        is None
    )
    assert (
        parse_access_preset_override({"accessPresetOverride": "read_only"})
        is ExecutionAccessPreset.READ_ONLY
    )
    with pytest.raises(ValueError, match="invalid Session access"):
        parse_access_preset_override({"access_preset_override": "corrupt"})


def test_permission_rules_are_normalized_ordered_and_frozen_in_profile() -> None:
    rules = normalize_permission_rules(
        {
            "bash": {"git push *": "ask", "*": "allow"},
            "write": "deny",
        }
    )
    assert rules == (
        ExecutionPermissionRuleSnapshot(
            permission="bash",
            pattern="*",
            action=ExecutionPermissionRuleAction.ALLOW,
        ),
        ExecutionPermissionRuleSnapshot(
            permission="bash",
            pattern="git push *",
            action=ExecutionPermissionRuleAction.ASK,
        ),
        ExecutionPermissionRuleSnapshot(
            permission="write",
            pattern="*",
            action=ExecutionPermissionRuleAction.DENY,
        ),
    )
    profile = ExecutionSecurityProfile.for_preset(
        ExecutionAccessPreset.ASK,
        permission_rules=rules,
    )
    assert ExecutionSecurityProfile.from_dict(profile.to_dict()) == profile


def test_old_serialized_profile_without_rules_remains_compatible() -> None:
    profile = ExecutionSecurityProfile.from_dict(
        {
            "accessPreset": "ask",
            "permissionMode": "default",
            "commandSandbox": True,
            "filesystemScope": "workspace",
            "approvalPolicy": "on_request",
        }
    )

    assert profile is not None
    assert profile.permission_rules == ()


def test_preset_profile_rejects_noncanonical_security_facts() -> None:
    with pytest.raises(ValueError, match="do not match"):
        ExecutionSecurityProfile(
            access_preset=ExecutionAccessPreset.FULL_ACCESS,
            permission_mode=ExecutionPermissionMode.FULL_AUTO,
            command_sandbox=True,
            filesystem_scope=FilesystemScope.UNRESTRICTED,
            approval_policy=ApprovalPolicy.NEVER,
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"accessPreset": "unknown"},
        {
            "accessPreset": "ask",
            "permissionMode": "default",
            "commandSandbox": "yes",
            "filesystemScope": "workspace",
            "approvalPolicy": "on_request",
        },
        {
            "accessPreset": "full_access",
            "permissionMode": "full_auto",
            "commandSandbox": True,
            "filesystemScope": "unrestricted",
            "approvalPolicy": "never",
        },
    ],
)
def test_invalid_serialized_security_profile_fails_closed(value: object) -> None:
    assert ExecutionSecurityProfile.from_dict(value) is None
