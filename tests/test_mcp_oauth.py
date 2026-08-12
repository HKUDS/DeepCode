from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from core.mcp.models import McpServerDefinition, McpServerSource, ResolvedMcpServer
from core.mcp.oauth import (
    McpAuthorizationCancelledError,
    McpOAuthCredentialStore,
    McpOAuthManager,
    create_mcp_oauth_provider,
)
from core.mcp.probe import probe_mcp_server

OAUTH_FIXTURE = Path(__file__).parent / "fixtures" / "mcp_oauth_server.py"


@pytest.mark.asyncio
async def test_oauth_storage_is_endpoint_scoped_private_and_logout_is_final(
    tmp_path,
) -> None:
    path = tmp_path / "auth" / "mcp.json"
    storage = McpOAuthCredentialStore(
        "notion",
        "https://mcp.notion.test/mcp",
        path=path,
    )
    await storage.prepare(
        "http://127.0.0.1:43210/auth/mcp/callback",
        reset=True,
    )
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="deepcode-test",
            redirect_uris=["http://127.0.0.1:43210/auth/mcp/callback"],
        )
    )
    await storage.set_tokens(
        OAuthToken(access_token="access-secret", refresh_token="refresh-secret")
    )

    reopened = McpOAuthCredentialStore(
        "notion",
        "https://mcp.notion.test/mcp",
        path=path,
    )
    assert reopened.has_tokens() is True
    assert (await reopened.get_tokens()).access_token == "access-secret"
    assert (
        McpOAuthCredentialStore(
            "notion",
            "https://replacement.test/mcp",
            path=path,
        ).has_tokens()
        is False
    )
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    assert McpOAuthCredentialStore.delete_server("notion", path=path) is True
    assert reopened.has_tokens() is False
    with pytest.raises(McpAuthorizationCancelledError):
        await storage.set_tokens(OAuthToken(access_token="must-not-return"))


@pytest.mark.asyncio
async def test_oauth_storage_can_delete_only_one_same_name_endpoint(tmp_path) -> None:
    path = tmp_path / "auth" / "mcp.json"
    first = McpOAuthCredentialStore("shared", "https://one.test/mcp", path=path)
    second = McpOAuthCredentialStore("shared", "https://two.test/mcp", path=path)
    await first.set_tokens(OAuthToken(access_token="first"))
    await second.set_tokens(OAuthToken(access_token="second"))

    assert (
        McpOAuthCredentialStore.delete_endpoint(
            "shared", "https://one.test/mcp", path=path
        )
        is True
    )
    assert (
        McpOAuthCredentialStore(
            "shared", "https://one.test/mcp", path=path
        ).has_tokens()
        is False
    )
    assert (
        McpOAuthCredentialStore(
            "shared", "https://two.test/mcp", path=path
        ).has_tokens()
        is True
    )


def test_oauth_browser_callback_persists_tokens_and_reconnects_noninteractively(
    tmp_path,
    monkeypatch,
) -> None:
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    process = subprocess.Popen(
        [sys.executable, str(OAUTH_FIXTURE), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    manager = McpOAuthManager()
    try:
        _wait_for_server(base_url)
        server = ResolvedMcpServer(
            server_id="oauth-fixture",
            name="oauth-fixture",
            source=McpServerSource.USER,
            definition=McpServerDefinition.model_validate(
                {
                    "type": "streamableHttp",
                    "url": f"{base_url}/mcp",
                    "auth": "oauth",
                    "startupTimeoutSeconds": 5,
                }
            ),
            config_dir=tmp_path,
            workspace=tmp_path,
        )

        started = manager.start(server, open_browser=False)
        assert started.status == "authorization_required"
        assert started.authorization_url is not None
        callback = httpx.get(started.authorization_url, follow_redirects=True)
        assert callback.status_code == 200

        completed = manager.wait(started.flow_id, timeout=10)
        assert completed.status == "authenticated"
        assert manager.status(server) == "authenticated"

        probe = asyncio.run(
            probe_mcp_server(
                server,
                oauth_provider_factory=create_mcp_oauth_provider,
            )
        )
        assert probe.ok is True
        assert probe.tool_count == 1
        assert manager.logout(server) is True
        assert manager.status(server) == "login_required"
    finally:
        manager.close()
        process.terminate()
        process.wait(timeout=5)


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(base_url: str) -> None:
    deadline = time.monotonic() + 10
    endpoint = f"{base_url}/.well-known/oauth-authorization-server"
    while time.monotonic() < deadline:
        try:
            if httpx.get(endpoint, timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise TimeoutError("OAuth MCP fixture did not start")
