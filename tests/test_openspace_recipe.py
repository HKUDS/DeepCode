from __future__ import annotations

import json
from pathlib import Path

from core.mcp import McpServerDefinition


def test_openspace_recipe_uses_generic_mcp_and_private_credentials() -> None:
    root = Path(__file__).parents[1]
    recipe = json.loads(
        (root / "examples" / "mcp" / "openspace.deepcode_config.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(recipe) == {"mcpServers"}
    definition = McpServerDefinition.model_validate(recipe["mcpServers"]["openspace"])
    assert definition.type == "stdio"
    assert definition.command == "openspace-mcp"
    assert definition.cwd == "${workspace}"
    assert definition.env["OPENSPACE_WORKSPACE"] == "${workspace}"
    assert definition.env["OPENSPACE_HOST_SKILL_DIRS"] == (
        "${workspace}/.agents/skills"
    )
    assert definition.tool_timeout_seconds >= 600
    assert definition.credential_env["OPENROUTER_API_KEY"].connection_id == (
        "openrouter"
    )
    assert "OPENROUTER_API_KEY" not in definition.env
    assert "upload_skill" in definition.disabled_tools
