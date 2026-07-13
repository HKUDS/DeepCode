"""Tests for the update_plan tool (C1 — model-driven working style)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.plan import UpdatePlanTool  # noqa: E402


def _run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


def test_records_and_renders_plan():
    tool = UpdatePlanTool()
    out = _run(
        tool,
        explanation="starting",
        plan=[
            {"step": "read the code", "status": "in_progress"},
            {"step": "write the fix", "status": "pending"},
        ],
    )
    assert "starting" in out
    assert "read the code" in out and "write the fix" in out
    assert "▶" in out and "☐" in out  # in_progress + pending glyphs
    assert "0/2 done" in out
    assert tool.plan == [
        {"step": "read the code", "status": "in_progress"},
        {"step": "write the fix", "status": "pending"},
    ]


def test_progress_replaces_plan_and_counts_done():
    tool = UpdatePlanTool()
    _run(tool, plan=[{"step": "a", "status": "in_progress"}])
    out = _run(
        tool,
        plan=[
            {"step": "a", "status": "completed"},
            {"step": "b", "status": "in_progress"},
        ],
    )
    assert "1/2 done" in out and "☑" in out


def test_at_most_one_in_progress():
    tool = UpdatePlanTool()
    out = _run(
        tool,
        plan=[
            {"step": "a", "status": "in_progress"},
            {"step": "b", "status": "in_progress"},
        ],
    )
    assert "at most one step" in out
    assert tool.plan == []  # rejected → nothing recorded


def test_rejects_bad_status_and_empty():
    tool = UpdatePlanTool()
    assert "must be one of" in _run(tool, plan=[{"step": "a", "status": "doing"}])
    assert "non-empty list" in _run(tool, plan=[])
    assert "step is required" in _run(tool, plan=[{"step": "", "status": "pending"}])


def test_is_side_effect_free():
    assert UpdatePlanTool().read_only is True


def test_wired_and_not_permission_gated():
    from core.harness.permissions import PermissionEngine, PermissionMode
    from core.harness.tools import default_coding_tools

    reg = default_coding_tools("/tmp")
    assert "update_plan" in set(reg.tool_names)

    # In default mode a mutating tool would ASK; update_plan must be allowed.
    engine = PermissionEngine(mode=PermissionMode.DEFAULT)
    decision, _ = engine.evaluate("update_plan", {"plan": []})
    assert decision.value == "allow"
