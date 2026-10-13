"""A configuration the user must fix is reported as such, not as a crash."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from app_server.errors import RpcError
from app_server.native_client import NativeRpcClient
from core.domain import TrustState
from tests.app_server.support import control_server
from tests.app_server.test_native_client import INITIALIZE, publish


def test_an_invalid_mcp_entry_names_itself_and_does_not_block_new_sessions(
    tmp_path,
):
    # One mcpServers entry without its "type" made thread/start answer
    # "internal error", with nothing saying which setting was wrong.
    config = tmp_path / "deepcode-home" / "deepcode_config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"mcpServers": {"slow": {"command": "python"}}}))
    assert os.environ["DEEPCODE_HOME"] == str(config.parent)

    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = control.host.application.projects.add(
                str(workspace), trust_state=TrustState.TRUSTED
            )
            client = NativeRpcClient(files)
            try:
                await client.connect(INITIALIZE)
                started = await client.request(
                    "thread/start", {"projectId": project.id, "title": "New"}
                )
                with pytest.raises(RpcError) as raised:
                    await client.request("settings/read", {"projectId": project.id})
            finally:
                await client.close()
                files.clear()
                lease.close()
        assert started["thread"]["title"] == "New"
        assert raised.value.stable_code == "CONFIG_INVALID"
        assert "mcpServers.slow.type" in str(raised.value)

    asyncio.run(scenario())
