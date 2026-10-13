"""The environment the workflow MCP client hands a stdio server.

``core.agent_runtime.tools.mcp`` (used by the Paper2Code workflows) passes a
server the user's environment, unlike ``core.mcp`` which passes only the
launch essentials. Either way a server must not receive the keys DeepCode's
own model connections use unless its config passes one on deliberately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.agent_runtime.tools.mcp import connect_mcp_servers
from core.agent_runtime.tools.registry import ToolRegistry
from core.config import DeepCodeConfig

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"


async def _read_from_server(names: list[str], env: dict[str, str]) -> list[str]:
    registry = ToolRegistry()
    config = DeepCodeConfig.model_validate(
        {
            "tools": {
                "mcpServers": {
                    "fixture": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(FIXTURE_SERVER)],
                        "env": env,
                    }
                }
            }
        }
    )
    await connect_mcp_servers(config.mcp_servers, registry)
    try:
        read = registry.get("mcp_fixture_read_value")
        assert read is not None, registry.tool_names
        return [str(await read.execute(name=name)) for name in names]
    finally:
        await registry.aclose()


@pytest.mark.asyncio
async def test_stdio_server_never_sees_deepcode_model_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-deepcode-own")
    monkeypatch.setenv("DEEPCODE_TEST_USER_SETTING", "user-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ambient")

    key, setting, forwarded = await _read_from_server(
        ["OPENAI_API_KEY", "DEEPCODE_TEST_USER_SETTING", "DEEPSEEK_API_KEY"],
        # A key the server's own config passes on is a deliberate act.
        env={"DEEPSEEK_API_KEY": "from-server-config"},
    )

    assert key == "<missing>"
    assert setting == "user-value"
    assert forwarded == "from-server-config"
