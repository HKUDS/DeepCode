"""Tests for collaboration modes (C1 — working-style preamble)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.collaboration import collaboration_preamble  # noqa: E402
from core.harness.permissions import PermissionMode  # noqa: E402


def test_plan_mode_preamble_is_non_mutating_and_plan_first():
    text = collaboration_preamble(PermissionMode.PLAN)
    assert "plan mode" in text.lower()
    assert "do not modify" in text.lower()
    assert "read-only" in text.lower()


def test_default_and_full_auto_are_autonomous():
    for mode in (PermissionMode.DEFAULT, PermissionMode.FULL_AUTO):
        text = collaboration_preamble(mode)
        assert "autonomously" in text.lower()
        assert "plan mode" not in text.lower()


def test_only_plan_mode_differs():
    plan = collaboration_preamble(PermissionMode.PLAN)
    default = collaboration_preamble(PermissionMode.DEFAULT)
    assert plan != default
