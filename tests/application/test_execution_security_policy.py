from __future__ import annotations

from types import SimpleNamespace

import core.application.execution_security_policy as policy_module
from core.application.execution_security_policy import ExecutionSecurityPolicy
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
    FilesystemScope,
)
from core.domain.runtime_coordination import ExecutionClass
from core.domain.thread import Thread, ThreadMode
from core.domain.turn import Turn


def _thread(
    tmp_path,
    preset: ExecutionAccessPreset | None = None,
) -> Thread:
    return Thread(
        project_id="proj_security",
        title="security",
        mode=ThreadMode.CODE,
        workspace_path=str(tmp_path),
        access_preset_override=preset,
    )


def _config(
    *,
    access_preset=None,
    permission_mode="full_auto",
    sandbox=True,
    permissions=None,
):
    fields = set()
    if access_preset is not None:
        fields.add("access_preset")
    return SimpleNamespace(
        security=SimpleNamespace(
            access_preset=access_preset,
            permission_mode=permission_mode,
            sandbox=sandbox,
            permissions=permissions or {},
            model_fields_set=fields,
        )
    )


def test_session_full_access_resolves_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: _config(),
    )

    profile = ExecutionSecurityPolicy().resolve(
        _thread(tmp_path, ExecutionAccessPreset.FULL_ACCESS)
    )

    assert profile == ExecutionSecurityProfile.for_preset(
        ExecutionAccessPreset.FULL_ACCESS
    )
    assert profile.permission_mode is ExecutionPermissionMode.FULL_AUTO
    assert profile.command_sandbox is False
    assert profile.filesystem_scope is FilesystemScope.UNRESTRICTED
    assert profile.approval_policy is ApprovalPolicy.NEVER


def test_configured_default_is_shared_when_session_inherits(tmp_path, monkeypatch):
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: _config(access_preset="read_only"),
    )

    profile = ExecutionSecurityPolicy().resolve(_thread(tmp_path))

    assert profile.access_preset is ExecutionAccessPreset.READ_ONLY


def test_permission_rules_are_frozen_when_the_turn_policy_is_resolved(
    tmp_path, monkeypatch
):
    config = _config(
        access_preset="ask",
        permissions={"bash": {"git push *": "deny"}},
    )
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: config,
    )

    profile = ExecutionSecurityPolicy().resolve(_thread(tmp_path))
    config.security.permissions["bash"]["git push *"] = "allow"

    assert [rule.action.value for rule in profile.permission_rules] == ["deny"]


def test_legacy_settings_are_snapshotted_without_claiming_full_access(
    tmp_path, monkeypatch
):
    config = _config(permission_mode="full_auto", sandbox=True)
    config.security.model_fields_set.add("permission_mode")
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: config,
    )

    profile = ExecutionSecurityPolicy().resolve(_thread(tmp_path))

    assert profile.access_preset is None
    assert profile.permission_mode is ExecutionPermissionMode.FULL_AUTO
    assert profile.command_sandbox is True
    assert profile.filesystem_scope is FilesystemScope.WORKSPACE


def test_automation_goal_keeps_owner_policy_but_interactive_goal_can_switch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: _config(),
    )
    plan = ExecutionSecurityProfile.from_legacy(
        ExecutionPermissionMode.PLAN,
        command_sandbox=True,
    )
    automation_turn = Turn(
        thread_id="thr_security",
        ordinal=1,
        prompt="run",
        goal_id="goal_security",
        execution_class=ExecutionClass.SCHEDULED_AUTOMATION,
        execution_permission_mode=ExecutionPermissionMode.PLAN,
        execution_security_profile=plan,
    )
    selected = _thread(tmp_path, ExecutionAccessPreset.FULL_ACCESS)

    inherited = ExecutionSecurityPolicy().resolve(
        selected,
        goal_turns=(automation_turn,),
    )
    assert inherited == plan

    later_full_access = Turn(
        thread_id="thr_security",
        ordinal=2,
        prompt="later",
        goal_id="goal_security",
        execution_class=ExecutionClass.GOAL_CONTINUATION,
        execution_permission_mode=ExecutionPermissionMode.FULL_AUTO,
        execution_security_profile=ExecutionSecurityProfile.for_preset(
            ExecutionAccessPreset.FULL_ACCESS
        ),
    )
    still_inherited = ExecutionSecurityPolicy().resolve(
        selected,
        goal_turns=(automation_turn, later_full_access),
    )
    assert still_inherited == plan

    interactive_turn = Turn(
        thread_id="thr_security",
        ordinal=1,
        prompt="run",
        goal_id="goal_security",
        execution_class=ExecutionClass.INTERACTIVE,
        execution_permission_mode=ExecutionPermissionMode.PLAN,
        execution_security_profile=plan,
    )
    switched = ExecutionSecurityPolicy().resolve(
        selected,
        goal_turns=(interactive_turn,),
    )
    assert switched.access_preset is ExecutionAccessPreset.FULL_ACCESS


def test_legacy_automation_owner_without_complete_snapshot_fails_read_only(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        policy_module,
        "load_config_for_workspace",
        lambda _workspace: _config(),
    )
    legacy_owner = Turn(
        thread_id="thr_security",
        ordinal=1,
        prompt="legacy automatic run",
        goal_id="goal_security",
        execution_class=ExecutionClass.MANUAL_AUTOMATION,
        execution_permission_mode=ExecutionPermissionMode.FULL_AUTO,
    )

    inherited = ExecutionSecurityPolicy().resolve(
        _thread(tmp_path, ExecutionAccessPreset.FULL_ACCESS),
        goal_turns=(legacy_owner,),
    )

    assert inherited.access_preset is ExecutionAccessPreset.READ_ONLY
    assert inherited.permission_mode is ExecutionPermissionMode.PLAN
