"""Tests for config-driven permission engine construction (P1.c5)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import SecurityConfig
from core.domain.execution_security import (
    ApprovalPolicy,
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
    FilesystemScope,
    normalize_permission_rules,
)
from core.harness.permissions import PermissionDecision, PermissionMode
from core.harness.policy import (
    build_permission_engine,
    resolve_execution_security_profile,
    resolve_permission_mode,
)


def _cfg(mode="full_auto", permissions=None):
    return SimpleNamespace(permission_mode=mode, permissions=permissions or {})


def test_legacy_default_mode_is_full_auto(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode(None) is PermissionMode.FULL_AUTO


def test_config_mode_used_when_no_env(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode("plan") is PermissionMode.PLAN


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "default")
    assert resolve_permission_mode("plan") is PermissionMode.DEFAULT


def test_typo_falls_back_to_client_default(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode("nonsense") is PermissionMode.FULL_AUTO
    assert (
        resolve_permission_mode("nonsense", default_mode=PermissionMode.DEFAULT)
        is PermissionMode.DEFAULT
    )


def test_build_engine_applies_config_rules(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    cfg = _cfg(
        mode="default",
        permissions={"execute_bash": {"git push *": "ask", "*": "allow"}},
    )
    engine = build_permission_engine(cfg, cwd="/w")
    assert engine.mode is PermissionMode.DEFAULT
    assert (
        engine.evaluate("execute_bash", {"command": "git status"})[0]
        is PermissionDecision.ALLOW
    )
    assert (
        engine.evaluate("execute_bash", {"command": "git push origin main"})[0]
        is PermissionDecision.ASK
    )


def test_build_engine_denylist_still_wins_over_config(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    cfg = _cfg(mode="full_auto", permissions={"read_file": {"*": "allow"}})
    engine = build_permission_engine(cfg, cwd="/home/u/proj")
    assert (
        engine.evaluate("read_file", {"file_path": "/home/u/.ssh/id_rsa"})[0]
        is PermissionDecision.DENY
    )


def test_none_config_preserves_legacy_cli_default():
    engine = build_permission_engine(None, cwd="/w")
    assert engine.mode is PermissionMode.FULL_AUTO
    assert engine.rules == []


def test_client_default_applies_only_when_pydantic_field_was_not_configured():
    implicit = SecurityConfig()
    desktop = build_permission_engine(
        implicit,
        cwd="/w",
        default_mode=PermissionMode.DEFAULT,
    )
    explicit = build_permission_engine(
        SecurityConfig(permission_mode="full_auto"),
        cwd="/w",
        default_mode=PermissionMode.DEFAULT,
    )

    assert desktop.mode is PermissionMode.DEFAULT
    assert explicit.mode is PermissionMode.FULL_AUTO


def test_mode_override_inherits_resolved_parent_policy(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "full_auto")
    engine = build_permission_engine(
        SecurityConfig(permission_mode="plan"),
        cwd="/w",
        mode_override=PermissionMode.DEFAULT,
    )
    assert engine.mode is PermissionMode.DEFAULT


def test_full_access_profile_is_atomic_and_ignores_legacy_env(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "plan")
    monkeypatch.setenv("DEEPCODE_SANDBOX", "1")
    full_access = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.FULL_ACCESS)

    resolved = resolve_execution_security_profile(
        SecurityConfig(permission_mode="default", sandbox=True),
        profile_override=full_access,
    )
    engine = build_permission_engine(
        SecurityConfig(permission_mode="default", sandbox=True),
        cwd="/w",
        execution_security_profile=full_access,
    )

    assert resolved is full_access
    assert resolved.command_sandbox is False
    assert resolved.filesystem_scope is FilesystemScope.UNRESTRICTED
    assert resolved.approval_policy is ApprovalPolicy.NEVER
    assert engine.mode is PermissionMode.FULL_AUTO
    assert engine.approval_policy is ApprovalPolicy.NEVER
    assert engine.protect_sensitive_paths is False
    assert (
        engine.evaluate("read", {"file_path": "/home/u/.ssh/id_rsa"})[0]
        is PermissionDecision.ALLOW
    )


def test_read_only_profile_is_a_non_expandable_upper_bound():
    read_only = ExecutionSecurityProfile.for_preset(ExecutionAccessPreset.READ_ONLY)
    engine = build_permission_engine(
        SecurityConfig(
            permission_mode="full_auto",
            permissions={"write": {"*": "allow"}},
        ),
        cwd="/w",
        execution_security_profile=read_only,
    )

    assert engine.mode is PermissionMode.PLAN
    assert engine.enforce_read_only is True
    assert (
        engine.evaluate("write", {"file_path": "/w/a.py"})[0] is PermissionDecision.DENY
    )


def test_engine_consumes_frozen_profile_rules_not_live_config():
    profile = ExecutionSecurityProfile.for_preset(
        ExecutionAccessPreset.ASK,
        permission_rules=normalize_permission_rules({"bash": {"git push *": "deny"}}),
    )
    engine = build_permission_engine(
        SecurityConfig(
            permission_mode="full_auto",
            permissions={"bash": {"*": "allow"}},
        ),
        cwd="/w",
        execution_security_profile=profile,
    )

    assert (
        engine.evaluate("bash", {"command": "git push origin main"})[0]
        is PermissionDecision.DENY
    )


def test_legacy_profile_honors_config_sandbox_and_env_override(monkeypatch):
    monkeypatch.delenv("DEEPCODE_SANDBOX", raising=False)
    configured = resolve_execution_security_profile(
        SecurityConfig(
            permission_mode="full_auto",
            sandbox=False,
            permissions={"bash": {"git push *": "ask"}},
        )
    )
    assert configured.access_preset is None
    assert configured.command_sandbox is False
    assert configured.permission_rules == normalize_permission_rules(
        {"bash": {"git push *": "ask"}}
    )

    monkeypatch.setenv("DEEPCODE_SANDBOX", "1")
    overridden = resolve_execution_security_profile(
        SecurityConfig(permission_mode="full_auto", sandbox=False)
    )
    assert overridden.command_sandbox is True


@pytest.mark.parametrize("bad_action", ["maybe", "sometimes"])
def test_invalid_config_action_raises(bad_action):
    with pytest.raises(ValueError):
        build_permission_engine(
            _cfg(permissions={"write_file": {"*": bad_action}}), cwd="/w"
        )
