"""Focused tests for Desktop timeline projection semantics."""

import pytest

from core.application.turn_projection import TurnEventProjector
from core.domain.item import ItemKind
from core.events import (
    PlanStepStatus,
    ToolActivityKind,
    describe_tool_activity,
    parse_plan_update,
)


def test_agent_tools_project_to_stable_desktop_item_kinds() -> None:
    assert TurnEventProjector._kind_for_tool("update_plan") is ItemKind.PLAN
    assert TurnEventProjector._kind_for_tool("bash") is ItemKind.COMMAND_EXECUTION
    assert TurnEventProjector._kind_for_tool("apply_patch") is ItemKind.FILE_CHANGE
    assert TurnEventProjector._kind_for_tool("grep") is ItemKind.TOOL_CALL


def test_tool_activity_semantics_are_central_and_typed() -> None:
    read = describe_tool_activity("read", {"file_path": "src/app.py"})
    search = describe_tool_activity("grep", {"pattern": "TurnEventProjector"})
    command = describe_tool_activity("bash", {"command": "pytest -q"})

    assert (read.kind, read.label, read.subject) == (
        ToolActivityKind.READ,
        "Read",
        "src/app.py",
    )
    assert search.kind is ToolActivityKind.SEARCH
    assert search.subject == "TurnEventProjector"
    assert command.kind is ToolActivityKind.RUN
    assert command.subject == "pytest -q"


def test_plan_parser_is_shared_and_rejects_ambiguous_progress() -> None:
    update = parse_plan_update(
        {
            "explanation": "Checking",
            "plan": [
                {"step": "Inspect", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ],
        }
    )
    assert update.explanation == "Checking"
    assert update.plan[0].status is PlanStepStatus.IN_PROGRESS

    with pytest.raises(ValueError, match="at most one step"):
        parse_plan_update(
            {
                "plan": [
                    {"step": "One", "status": "in_progress"},
                    {"step": "Two", "status": "in_progress"},
                ]
            }
        )
