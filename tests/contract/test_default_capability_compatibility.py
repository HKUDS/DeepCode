"""Compatibility gates for opt-in runtime capability work.

These tests intentionally describe the observable baseline rather than exact
descriptions or serialized JSON ordering.  New capabilities may extend the
runtime when enabled, but leaving them disabled must keep this contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.agent_runtime.tools.base import ToolResult
from core.domain import ItemKind, ItemStatus, TurnExecutor, TurnStatus
from core.domain.runtime_coordination import ExecutionClass
from core.harness.tools import default_coding_tools
from core.persistence import Database, ItemRepository, TurnRepository
from core.persistence.migrations import LATEST_SCHEMA_VERSION, current_version

_DEFAULT_TOOL_PARAMETERS = {
    "read": {
        "file_path": ("string", True),
        "offset": ("integer", False),
        "limit": ("integer", False),
    },
    "write": {
        "file_path": ("string", True),
        "content": ("string", True),
    },
    "edit": {
        "file_path": ("string", True),
        "old_string": ("string", True),
        "new_string": ("string", True),
        "replace_all": ("boolean", False),
    },
    "apply_patch": {"patch": ("string", True)},
    "bash": {
        "command": ("string", True),
        "timeout": ("integer", False),
    },
    "grep": {
        "pattern": ("string", True),
        "path": ("string", False),
        "include": ("string", False),
    },
    "glob": {
        "pattern": ("string", True),
        "path": ("string", False),
    },
    "memory": {
        "action": ("string", True),
        "name": ("string", False),
        "content": ("string", False),
    },
    "update_plan": {
        "explanation": ("string", False),
        "plan": ("array", True),
    },
    "web_fetch": {"url": ("string", True)},
}


def test_default_coding_tools_include_the_minimal_web_fetch_contract(
    tmp_path: Path,
) -> None:
    """Every shared Agent surface receives one provider-free URL reader."""

    registry = default_coding_tools(tmp_path, skills=())
    definitions = {
        definition["function"]["name"]: definition
        for definition in registry.get_definitions()
    }

    expected_names = set(_DEFAULT_TOOL_PARAMETERS)
    assert set(registry.tool_names) == expected_names
    assert set(definitions) == expected_names

    for tool_name, expected_properties in _DEFAULT_TOOL_PARAMETERS.items():
        definition = definitions[tool_name]
        assert definition["type"] == "function"
        function = definition["function"]
        assert function["name"] == tool_name
        assert isinstance(function["description"], str)
        assert function["description"].strip()

        parameters = function["parameters"]
        assert parameters["type"] == "object"
        properties = parameters["properties"]
        required = set(parameters.get("required", ()))
        assert set(properties) == set(expected_properties)
        assert required == {
            name
            for name, (_, is_required) in expected_properties.items()
            if is_required
        }
        for parameter_name, (parameter_type, _) in expected_properties.items():
            assert properties[parameter_name]["type"] == parameter_type

    assert set(
        definitions["memory"]["function"]["parameters"]["properties"]["action"]["enum"]
    ) == {"list", "read", "write", "append", "delete"}

    plan_items = definitions["update_plan"]["function"]["parameters"]["properties"][
        "plan"
    ]["items"]
    assert plan_items["type"] == "object"
    assert set(plan_items["properties"]) == {"step", "status"}
    assert set(plan_items["required"]) == {"step", "status"}
    assert plan_items["properties"]["step"]["type"] == "string"
    assert plan_items["properties"]["status"]["type"] == "string"
    assert set(plan_items["properties"]["status"]["enum"]) == {
        "pending",
        "in_progress",
        "completed",
    }


def test_tool_result_preserves_string_and_metadata_contract() -> None:
    metadata = {"exit_code": 7, "process_id": "proc_example"}

    result = ToolResult(
        "verification output",
        is_error=True,
        metadata=metadata,
    )
    metadata["exit_code"] = 0

    assert isinstance(result, str)
    assert str(result) == "verification output"
    assert result.is_error is True
    assert result.metadata == {"exit_code": 7, "process_id": "proc_example"}

    replacement = result.with_content("replacement output")
    assert isinstance(replacement, ToolResult)
    assert str(replacement) == "replacement output"
    assert replacement.is_error is True
    assert replacement.metadata == result.metadata
    assert replacement.metadata is not result.metadata

    result.metadata["exit_code"] = 9
    assert replacement.metadata["exit_code"] == 7
    assert ToolResult("plain").metadata == {}
    assert ToolResult("plain").is_error is False


def test_v1_turn_and_item_remain_readable_after_upgrade(tmp_path: Path) -> None:
    """A Session persisted by the first schema survives every migration."""

    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize(target_version=1)
    workspace = tmp_path / "legacy-workspace"
    timestamp = "2025-01-02T03:04:05+00:00"
    legacy_payload = {
        "content": "legacy reasoning summary",
        "provider": "legacy-provider",
        "usage": {"outputTokens": 17},
    }

    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects "
            "(id, canonical_path, display_name, trust_state, settings_json, "
            "created_at, updated_at, last_opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "proj_legacy",
                str(workspace),
                "Legacy project",
                "trusted",
                "{}",
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO threads "
            "(id, project_id, parent_thread_id, title, mode, status, model, "
            "workspace_path, worktree_path, created_at, updated_at, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-session",
                "proj_legacy",
                None,
                "Legacy Session",
                "code",
                "idle",
                "legacy-model",
                str(workspace),
                None,
                timestamp,
                timestamp,
                None,
            ),
        )
        connection.execute(
            "INSERT INTO turns "
            "(id, thread_id, ordinal, prompt, status, stop_reason, error_code, "
            "error_message, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "turn_legacy",
                "legacy-session",
                1,
                "Inspect the legacy repository",
                "completed",
                "end_turn",
                None,
                None,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO items "
            "(id, thread_id, turn_id, ordinal, kind, status, summary, "
            "payload_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "item_legacy",
                "legacy-session",
                "turn_legacy",
                1,
                "reasoning_summary",
                "completed",
                "Legacy summary",
                json.dumps(legacy_payload),
                timestamp,
                timestamp,
            ),
        )

    database.initialize()

    with database.read() as connection:
        assert current_version(connection) == LATEST_SCHEMA_VERSION
        turn = TurnRepository(connection).get("turn_legacy")
        item = ItemRepository(connection).get("item_legacy")

    assert turn is not None
    assert turn.prompt == "Inspect the legacy repository"
    assert turn.status is TurnStatus.COMPLETED
    assert turn.stop_reason == "end_turn"
    assert turn.skill_ids == ()
    assert turn.execution_profile is None
    assert turn.execution_permission_mode is None
    assert turn.execution_security_profile is None
    assert turn.goal_id is None
    assert turn.executor is TurnExecutor.AGENT
    assert turn.execution_class is ExecutionClass.INTERACTIVE

    assert item is not None
    assert item.kind is ItemKind.REASONING_SUMMARY
    assert item.status is ItemStatus.COMPLETED
    assert item.summary == "Legacy summary"
    assert item.payload == legacy_payload
