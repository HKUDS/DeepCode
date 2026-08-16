"""Session-scoped MCP lifecycle and immutable tool-catalog publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.agent_runtime.tools.registry import ToolRegistry
from core.mcp.connection import CredentialResolver, McpConnection, OAuthProviderFactory
from core.mcp.models import McpRuntimePlan, McpStartupError
from core.mcp.naming import server_allowed, visible_tool_name
from core.mcp.tools import McpToolAdapter


@dataclass(frozen=True, slots=True)
class McpServerRuntimeStatus:
    server_id: str
    name: str
    source: str
    state: str
    tool_count: int
    error: str | None = None


class McpSessionRuntime:
    """Materialize one immutable MCP plan into a DeepCode ToolRegistry."""

    def __init__(
        self,
        plan: McpRuntimePlan,
        registry: ToolRegistry,
        *,
        credential_resolver: CredentialResolver | None = None,
        oauth_provider_factory: OAuthProviderFactory | None = None,
        status_observer: Callable[[tuple[McpServerRuntimeStatus, ...]], None]
        | None = None,
    ) -> None:
        self.plan = plan
        self.registry = registry
        self._credential_resolver = credential_resolver
        self._oauth_provider_factory = oauth_provider_factory
        self._status_observer = status_observer
        self._connections: dict[str, McpConnection] = {}
        self._registered_tools: tuple[str, ...] = ()
        self._capabilities: dict[str, tuple[str, ...]] = {}
        self._statuses: dict[str, McpServerRuntimeStatus] = {
            server.server_id: McpServerRuntimeStatus(
                server.server_id,
                server.name,
                server.source.value,
                "configured",
                0,
            )
            for server in plan.servers
        }
        self._lock = asyncio.Lock()
        self._started = False
        self._closed = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def available_server_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                server_id
                for server_id, connection in self._connections.items()
                if connection.ready
            )
        )

    @property
    def skill_capabilities(self) -> dict[str, tuple[str, ...]]:
        """Map Skill dependency names to exact model-visible tool names."""

        return dict(self._capabilities)

    @property
    def statuses(self) -> tuple[McpServerRuntimeStatus, ...]:
        return tuple(self._statuses[key] for key in sorted(self._statuses))

    def instruction_context(self) -> str | None:
        sections = []
        for server_id in sorted(self._connections):
            instruction = self._connections[server_id].instructions
            if instruction:
                sections.append(f"MCP server {server_id} instructions:\n{instruction}")
        if not sections:
            return None
        return "\n\n".join(sections)[:8_000]

    async def ensure_started(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("MCP runtime is closed")
            if self._started:
                return
            if not self.plan.servers:
                self._started = True
                return
            connections = {
                server.server_id: McpConnection(
                    server,
                    credential_resolver=self._credential_resolver,
                    oauth_provider_factory=self._oauth_provider_factory,
                )
                for server in self.plan.servers
            }
            for server_id, status in tuple(self._statuses.items()):
                self._statuses[server_id] = McpServerRuntimeStatus(
                    status.server_id,
                    status.name,
                    status.source,
                    "starting",
                    0,
                )
            self._publish_statuses()
            try:
                results = await asyncio.gather(
                    *(connection.start() for connection in connections.values()),
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                await asyncio.gather(
                    *(connection.close() for connection in connections.values()),
                    return_exceptions=True,
                )
                raise
            required_failures: list[str] = []
            ready: list[tuple[McpConnection, tuple[Any, ...]]] = []
            for connection, result in zip(connections.values(), results, strict=True):
                server = connection.server
                if isinstance(result, BaseException):
                    error = f"{type(result).__name__}: {result}"
                    self._statuses[server.server_id] = McpServerRuntimeStatus(
                        server.server_id,
                        server.name,
                        server.source.value,
                        "failed",
                        0,
                        error,
                    )
                    logger.warning("MCP server '{}' failed: {}", server.name, error)
                    await connection.close()
                    if server.definition.required:
                        required_failures.append(f"{server.name}: {error}")
                    continue
                self._connections[server.server_id] = connection
                ready.append((connection, result))

            if required_failures:
                await self._close_connections()
                self._publish_statuses()
                raise McpStartupError(
                    "Required MCP server startup failed: "
                    + "; ".join(required_failures)
                )

            used = set(self.registry.tool_names)
            registered: list[str] = []
            for connection, definitions in ready:
                server = connection.server
                seen_raw: set[str] = set()
                exposed_count = 0
                server_tools: list[str] = []
                for definition in sorted(definitions, key=lambda item: str(item.name)):
                    raw_name = str(definition.name)
                    if raw_name in seen_raw:
                        logger.warning(
                            "MCP server '{}' returned duplicate tool name '{}'",
                            server.name,
                            raw_name,
                        )
                        continue
                    seen_raw.add(raw_name)
                    if not server.definition.exposes(raw_name):
                        continue
                    # P1-9 (lesson 13): supply-chain allowlist — a server that
                    # is not on DEEPCODE_MCP_SERVER_ALLOWLIST registers no
                    # tools (remote MCP is the widest third-party surface).
                    if not server_allowed(server.server_id, server.name):
                        continue
                    name = visible_tool_name(server.server_id, raw_name, used=used)
                    adapter = McpToolAdapter(
                        connection,
                        definition,
                        visible_name=name,
                    )
                    self.registry.register(adapter)
                    registered.append(name)
                    server_tools.append(name)
                    exposed_count += 1
                self._warn_unmatched_filters(server, seen_raw)
                tools = tuple(server_tools)
                self._capabilities[server.server_id] = tools
                if server.policy_key is not None:
                    self._capabilities[server.policy_key] = tools
                    if (
                        server.plugin_id is not None
                        and server.plugin_server_name is not None
                    ):
                        self._capabilities[
                            f"{server.plugin_id}:{server.plugin_server_name}"
                        ] = tools
                self._statuses[server.server_id] = McpServerRuntimeStatus(
                    server.server_id,
                    server.name,
                    server.source.value,
                    "ready",
                    exposed_count,
                )
            self._registered_tools = tuple(registered)
            self._started = True
            self._publish_statuses()

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            for name in self._registered_tools:
                self.registry.unregister(name)
            self._registered_tools = ()
            self._capabilities.clear()
            await self._close_connections()
            for server_id, status in tuple(self._statuses.items()):
                self._statuses[server_id] = McpServerRuntimeStatus(
                    status.server_id,
                    status.name,
                    status.source,
                    "closed",
                    0,
                )
            self._publish_statuses()

    async def _close_connections(self) -> None:
        connections = tuple(self._connections.values())
        self._connections.clear()
        if connections:
            await asyncio.gather(
                *(connection.close() for connection in connections),
                return_exceptions=True,
            )

    @staticmethod
    def _warn_unmatched_filters(server, discovered: set[str]) -> None:
        enabled = server.definition.enabled_tools
        if enabled is None or "*" in enabled:
            return
        unmatched = sorted(set(enabled) - discovered)
        if unmatched:
            logger.warning(
                "MCP server '{}': enabledTools not found: {}",
                server.name,
                ", ".join(unmatched),
            )

    def _publish_statuses(self) -> None:
        if self._status_observer is None:
            return
        try:
            self._status_observer(self.statuses)
        except Exception:  # noqa: BLE001 - observer boundary
            logger.exception("MCP status observer failed")


__all__ = ["McpServerRuntimeStatus", "McpSessionRuntime"]
