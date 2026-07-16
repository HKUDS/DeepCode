"""Focused tests for Desktop timeline projection semantics."""

from core.application.turn_projection import TurnEventProjector
from core.domain.item import ItemKind


def test_agent_tools_project_to_stable_desktop_item_kinds() -> None:
    assert TurnEventProjector._kind_for_tool("update_plan") is ItemKind.PLAN
    assert TurnEventProjector._kind_for_tool("bash") is ItemKind.COMMAND_EXECUTION
    assert TurnEventProjector._kind_for_tool("apply_patch") is ItemKind.FILE_CHANGE
    assert TurnEventProjector._kind_for_tool("grep") is ItemKind.TOOL_CALL
