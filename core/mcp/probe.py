"""One-shot MCP connection verification shared by every product surface."""

from __future__ import annotations

import time
from dataclasses import dataclass

from core.mcp.connection import CredentialResolver, McpConnection, OAuthProviderFactory
from core.mcp.models import ResolvedMcpServer


@dataclass(frozen=True, slots=True)
class McpProbeResult:
    server_id: str
    name: str
    ok: bool
    transport: str
    tool_count: int
    resource_count: int
    prompt_count: int
    elapsed_seconds: float
    error: str | None = None


async def probe_mcp_server(
    server: ResolvedMcpServer,
    *,
    credential_resolver: CredentialResolver | None = None,
    oauth_provider_factory: OAuthProviderFactory | None = None,
) -> McpProbeResult:
    started = time.monotonic()
    connection = McpConnection(
        server,
        credential_resolver=credential_resolver,
        oauth_provider_factory=oauth_provider_factory,
    )
    try:
        tools = await connection.start()
        return McpProbeResult(
            server.server_id,
            server.name,
            True,
            server.definition.type,
            len(tools),
            connection.resource_count,
            connection.prompt_count,
            round(time.monotonic() - started, 3),
        )
    except BaseException as exc:  # noqa: BLE001 - typed probe result boundary
        return McpProbeResult(
            server.server_id,
            server.name,
            False,
            server.definition.type,
            0,
            0,
            0,
            round(time.monotonic() - started, 3),
            _safe_probe_error(exc, server),
        )
    finally:
        await connection.close()


def _safe_probe_error(exc: BaseException, server: ResolvedMcpServer) -> str:
    message = f"{type(exc).__name__}: {exc}"
    names = {
        *server.definition.required_env_vars,
        *server.definition.env_url_params.values(),
    }
    import os

    for name in names:
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "••••••")
    return message[:1_000]


__all__ = ["McpProbeResult", "probe_mcp_server"]
