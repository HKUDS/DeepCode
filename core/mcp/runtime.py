"""Session-scoped MCP lifecycle and immutable tool-catalog publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.agent_runtime.tools.base import Tool, ToolResult
from core.agent_runtime.tools.registry import ToolRegistry
from core.mcp.connection import CredentialResolver, McpConnection, OAuthProviderFactory
from core.mcp.models import McpRuntimePlan, McpStartupError
from core.mcp.naming import visible_tool_name
from core.mcp.tools import McpToolAdapter


@dataclass(frozen=True, slots=True)
class McpServerRuntimeStatus:
    server_id: str
    name: str
    source: str
    state: str
    tool_count: int
    error: str | None = None


class _ActivateMcpServerTool(Tool):
    """Model-visible bridge that makes deferred servers reachable."""

    def __init__(
        self,
        runtime: McpSessionRuntime,
        *,
        name: str,
        server_ids: tuple[str, ...],
    ) -> None:
        self._runtime = runtime
        self._name = name
        self._server_ids = server_ids

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        available = ", ".join(self._server_ids)
        return (
            "Start one configured deferred MCP server so its tools become "
            f"available to this session. Deferred server ids: {available}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "enum": list(self._server_ids),
                    "description": "Configured MCP server id to activate.",
                }
            },
            "required": ["server_id"],
            "additionalProperties": False,
        }

    def presentation_detail(self, arguments: dict[str, Any]) -> str | None:
        value = arguments.get("server_id")
        return value if isinstance(value, str) else ""

    async def execute(self, *, server_id: str) -> ToolResult:
        activated = await self._runtime.activate_server(server_id)
        if not activated:
            return ToolResult(
                f"Error: MCP server '{server_id}' could not be activated.",
                is_error=True,
                metadata={"serverId": server_id, "activated": False},
            )
        tools = self._runtime.skill_capabilities.get(server_id, ())
        suffix = f" Available tools: {', '.join(tools)}." if tools else ""
        instructions = self._runtime.server_instruction(server_id)
        if instructions:
            suffix += f"\n\nServer instructions:\n{instructions}"
        return ToolResult(
            f"MCP server '{server_id}' activated.{suffix}",
            metadata={
                "serverId": server_id,
                "activated": True,
                "tools": list(tools),
            },
        )


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

    def server_instruction(self, server_id: str) -> str | None:
        """Return bounded instructions for one active server."""

        connection = self._connections.get(server_id)
        if connection is None or not connection.instructions:
            return None
        return str(connection.instructions)[:4_000]

    async def ensure_started(self) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("MCP runtime is closed")
            if self._started:
                return
            if not self.plan.servers:
                self._started = True
                return
            deferred_ids = {
                server.server_id
                for server in self.plan.servers
                if server.definition.defer_loading
            }
            connections = {
                server.server_id: McpConnection(
                    server,
                    credential_resolver=self._credential_resolver,
                    oauth_provider_factory=self._oauth_provider_factory,
                )
                for server in self.plan.servers
                if server.server_id not in deferred_ids
            }
            for server_id, status in tuple(self._statuses.items()):
                self._statuses[server_id] = McpServerRuntimeStatus(
                    status.server_id,
                    status.name,
                    status.source,
                    "deferred" if server_id in deferred_ids else "starting",
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
                registered.extend(
                    self._register_server_tools(connection, definitions, used=used)
                )
            if deferred_ids:
                activation_name = visible_tool_name(
                    "deepcode_runtime",
                    "activate_server",
                    used=used,
                )
                self.registry.register(
                    _ActivateMcpServerTool(
                        self,
                        name=activation_name,
                        server_ids=tuple(sorted(deferred_ids)),
                    )
                )
                registered.append(activation_name)
            self._registered_tools = tuple(registered)
            self._started = True
            self._publish_statuses()

    async def activate_server(self, server_id: str) -> bool:
        """Start one MCP server on demand and register its tools.

        Deferred servers (``deferLoading: true``) are skipped by
        :meth:`ensure_started`; call this to bring one up when it is actually
        needed. Idempotent: returns ``True`` immediately when the server is
        already ready. Returns ``False`` for unknown servers or when startup
        fails (status is set to ``failed``).
        """
        if not self._started:
            if self._closed:
                return False
            try:
                await self.ensure_started()
            except RuntimeError:
                if self._closed:
                    return False
                raise
        async with self._lock:
            if self._closed:
                return False
            existing = self._connections.get(server_id)
            if existing is not None and existing.ready:
                return True
            server = next(
                (s for s in self.plan.servers if s.server_id == server_id),
                None,
            )
            if server is None:
                return False
            self._statuses[server_id] = McpServerRuntimeStatus(
                server.server_id,
                server.name,
                server.source.value,
                "starting",
                0,
            )
            connection = McpConnection(
                server,
                credential_resolver=self._credential_resolver,
                oauth_provider_factory=self._oauth_provider_factory,
            )
            try:
                definitions = await connection.start()
            except asyncio.CancelledError:
                await connection.close()
                self._statuses[server_id] = McpServerRuntimeStatus(
                    server.server_id,
                    server.name,
                    server.source.value,
                    "deferred" if server.definition.defer_loading else "failed",
                    0,
                )
                self._publish_statuses()
                raise
            except BaseException as exc:  # noqa: BLE001 - startup boundary
                error = f"{type(exc).__name__}: {exc}"
                self._statuses[server_id] = McpServerRuntimeStatus(
                    server.server_id,
                    server.name,
                    server.source.value,
                    "failed",
                    0,
                    error,
                )
                logger.warning(
                    "MCP server '{}' activation failed: {}", server.name, error
                )
                await connection.close()
                self._publish_statuses()
                return False
            self._connections[server_id] = connection
            registered = self._register_server_tools(
                connection,
                definitions,
                used=set(self.registry.tool_names),
            )
            self._registered_tools = (*self._registered_tools, *registered)
            self._publish_statuses()
            return True

    def _register_server_tools(
        self,
        connection: McpConnection,
        definitions: tuple[Any, ...],
        *,
        used: set[str],
    ) -> list[str]:
        """Register one ready server's tools; returns the visible names."""
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
            name = visible_tool_name(server.server_id, raw_name, used=used)
            adapter = McpToolAdapter(
                connection,
                definition,
                visible_name=name,
            )
            self.registry.register(adapter)
            server_tools.append(name)
            exposed_count += 1
        self._warn_unmatched_filters(server, seen_raw)
        tools = tuple(server_tools)
        self._capabilities[server.server_id] = tools
        if server.policy_key is not None:
            self._capabilities[server.policy_key] = tools
            if server.plugin_id is not None and server.plugin_server_name is not None:
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
        return server_tools

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
