"""Closest-name hints when the model calls a tool that is not registered."""

from __future__ import annotations

from typing import Any

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.agent_runtime.tools.semantic_hint import build_miss_message, suggest_tools


def test_suggest_tools_ranks_close_names_first():
    names = ["read_file", "write_file", "bash", "glob", "grep"]
    assert suggest_tools("readfile", names)[0] == "read_file"
    assert suggest_tools("write_files", names)[0] == "write_file"


def test_suggest_tools_is_silent_for_unrelated_names():
    assert suggest_tools("launch_rocket", ["read_file", "bash"]) == []
    assert build_miss_message("launch_rocket", ["read_file", "bash"]) == (
        "Tool 'launch_rocket' not found."
    )


def test_suggest_tools_never_returns_the_missed_name_itself():
    assert "bash" not in suggest_tools("bash", ["bash", "bash_tool"])


@tool_parameters({"type": "object", "properties": {}})
class _ReadFile(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file."

    async def execute(self, **kwargs: Any) -> Any:
        return ""


@tool_parameters({"type": "object", "properties": {}})
class _Bash(Tool):
    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Run a command."

    async def execute(self, **kwargs: Any) -> Any:
        return ""


def test_registry_miss_message_includes_a_hint_and_the_full_list():
    registry = ToolRegistry()
    registry.register(_ReadFile())
    registry.register(_Bash())
    tool, _params, error = registry.prepare_call("readfile", {})
    assert tool is None
    assert error is not None
    assert "Did you mean one of: read_file" in error
    assert "Available: read_file, bash" in error


def test_registry_miss_message_without_a_close_name_is_unchanged_in_spirit():
    registry = ToolRegistry()
    registry.register(_Bash())
    _tool, _params, error = registry.prepare_call("launch_rocket", {})
    assert error == "Error: Tool 'launch_rocket' not found. Available: bash"
