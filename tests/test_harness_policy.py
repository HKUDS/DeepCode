"""Tests for config-driven permission engine construction (P1.c5)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.permissions import PermissionDecision, PermissionMode  # noqa: E402
from core.harness.policy import (  # noqa: E402
    build_permission_engine,
    resolve_permission_mode,
)


def _cfg(mode="full_auto", permissions=None):
    return SimpleNamespace(permission_mode=mode, permissions=permissions or {})


def test_default_mode_is_full_auto(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode(None) is PermissionMode.FULL_AUTO


def test_config_mode_used_when_no_env(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode("plan") is PermissionMode.PLAN


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "default")
    assert resolve_permission_mode("plan") is PermissionMode.DEFAULT


def test_typo_falls_back_to_full_auto(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    assert resolve_permission_mode("nonsense") is PermissionMode.FULL_AUTO


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


def test_none_config_is_safe():
    engine = build_permission_engine(None, cwd="/w")
    assert engine.mode is PermissionMode.FULL_AUTO
    assert engine.rules == []


@pytest.mark.parametrize("bad_action", ["maybe", "sometimes"])
def test_invalid_config_action_raises(bad_action):
    with pytest.raises(ValueError):
        build_permission_engine(
            _cfg(permissions={"write_file": {"*": bad_action}}), cwd="/w"
        )
