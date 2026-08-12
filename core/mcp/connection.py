"""One-task transport ownership for a single MCP server connection."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from loguru import logger

from core.mcp.models import (
    McpConfigurationError,
    McpServerSource,
    ResolvedMcpServer,
)
from core.platform_compat import normalize_stdio_command

CredentialResolver = Callable[[str], str | None]
OAuthProviderFactory = Callable[[ResolvedMcpServer], Awaitable[httpx.Auth]]
MAX_DISCOVERED_TOOLS = 256
MAX_DISCOVERED_RESOURCES = 256
MAX_DISCOVERED_PROMPTS = 256
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpConnection:
    """Own one MCP transport and SDK session for its entire lifetime.

    The async context managers are entered and exited by ``_serve`` in the
    same task. Tool calls use child tasks so timeout and Turn interruption can
    cancel an individual request without corrupting transport teardown.
    """

    def __init__(
        self,
        server: ResolvedMcpServer,
        *,
        credential_resolver: CredentialResolver | None = None,
        oauth_provider_factory: OAuthProviderFactory | None = None,
    ) -> None:
        self.server = server
        self._credential_resolver = credential_resolver
        self._oauth_provider_factory = oauth_provider_factory
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[tuple[Any, ...]] | None = None
        self._close_event: asyncio.Event | None = None
        self._session: Any | None = None
        self._active_calls: set[asyncio.Task[Any]] = set()
        self._tools: tuple[Any, ...] = ()
        self.resource_count = 0
        self.prompt_count = 0
        self.instructions: str | None = None
        self.last_error: str | None = None

    @property
    def ready(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and self._session is not None
        )

    async def start(self, *, timeout_seconds: float | None = None) -> tuple[Any, ...]:
        if self.ready:
            return self._tools
        if self._task is not None:
            raise RuntimeError(f"MCP server {self.server.name!r} cannot be restarted")
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._close_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._serve(),
            name=f"deepcode-mcp-{self.server.server_id}",
        )
        timeout = timeout_seconds or self.server.definition.startup_timeout_seconds
        try:
            return await asyncio.wait_for(asyncio.shield(self._ready), timeout=timeout)
        except TimeoutError as exc:
            self.last_error = f"startup timed out after {timeout:g}s"
            await self.close()
            raise TimeoutError(
                f"MCP server {self.server.name!r} {self.last_error}"
            ) from exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._session
        if session is None or not self.ready:
            detail = self.last_error or "connection is not ready"
            raise RuntimeError(f"MCP server {self.server.name!r}: {detail}")
        timeout = self.server.definition.tool_timeout_seconds
        call = asyncio.create_task(
            session.call_tool(
                name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=timeout),
            ),
            name=f"deepcode-mcp-call-{self.server.server_id}-{name}",
        )
        self._active_calls.add(call)
        try:
            return await asyncio.wait_for(call, timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                f"MCP tool {self.server.name}.{name} timed out after {timeout:g}s"
            ) from exc
        finally:
            self._active_calls.discard(call)

    async def close(self) -> None:
        task = self._task
        if task is None:
            return
        calls = tuple(self._active_calls)
        for call in calls:
            if not call.done():
                call.cancel()
        if calls:
            await asyncio.gather(*calls, return_exceptions=True)
        if self._session is None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._session = None
            return
        if self._close_event is not None:
            self._close_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except BaseException as exc:  # noqa: BLE001 - failure already recorded
            logger.debug(
                "MCP server '{}' close observed {}",
                self.server.name,
                type(exc).__name__,
            )
        finally:
            self._session = None

    async def _serve(self) -> None:
        assert self._ready is not None
        assert self._close_event is not None
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(stack)
                from mcp import ClientSession

                session = await stack.enter_async_context(ClientSession(read, write))
                initialized = await session.initialize()
                self.instructions = _bounded_instructions(
                    getattr(initialized, "instructions", None)
                )
                capabilities = getattr(initialized, "capabilities", None)
                self._tools = (
                    await _list_all_tools(session)
                    if getattr(capabilities, "tools", None) is not None
                    else ()
                )
                self.resource_count = (
                    await _count_all_pages(
                        session.list_resources,
                        attribute="resources",
                        limit=MAX_DISCOVERED_RESOURCES,
                        label="resources",
                    )
                    if getattr(capabilities, "resources", None) is not None
                    else 0
                )
                self.prompt_count = (
                    await _count_all_pages(
                        session.list_prompts,
                        attribute="prompts",
                        limit=MAX_DISCOVERED_PROMPTS,
                        label="prompts",
                    )
                    if getattr(capabilities, "prompts", None) is not None
                    else 0
                )
                self._session = session
                self._ready.set_result(self._tools)
                await self._close_event.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except BaseException as exc:  # noqa: BLE001 - publish startup/runtime failure
            self.last_error = f"{type(exc).__name__}: {exc}"
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                logger.warning(
                    "MCP server '{}' disconnected: {}",
                    self.server.name,
                    self.last_error,
                )
        finally:
            self._session = None

    async def _open_transport(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        definition = self.server.definition
        if definition.type == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            command = _resolve_path_or_command(definition.command or "", self.server)
            args = [
                _resolve_placeholders(item, self.server) for item in definition.args
            ]
            cwd = _resolve_cwd(definition.cwd, self.server)
            env = self._stdio_environment()
            command, args, env = normalize_stdio_command(
                command,
                args,
                env,
                inherit_env=False,
            )
            params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
                cwd=cwd,
            )
            errlog = _open_stderr_log(self.server)
            if errlog is not None:
                stack.callback(errlog.close)
                return await stack.enter_async_context(
                    stdio_client(params, errlog=errlog)
                )
            return await stack.enter_async_context(stdio_client(params))

        headers = self._http_headers()
        url = self._http_url()
        auth = None
        if definition.auth == "oauth":
            if self._oauth_provider_factory is None:
                raise McpConfigurationError(
                    f"MCP server {self.server.name!r} requires OAuth authorization"
                )
            auth = await self._oauth_provider_factory(self.server)
        if definition.type == "sse":
            from mcp.client.sse import sse_client

            options: dict[str, Any] = {"headers": headers or None, "auth": auth}
            if self.server.source is McpServerSource.PLUGIN:
                options["httpx_client_factory"] = _plugin_http_client
            return await stack.enter_async_context(sse_client(url, **options))

        from mcp.client.streamable_http import streamable_http_client

        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=headers or None,
                auth=auth,
                # Agent Plugins configured headers may not cross origins. A
                # redirect is therefore surfaced as a connection failure;
                # native user/project servers retain normal redirect support.
                follow_redirects=self.server.source is not McpServerSource.PLUGIN,
                timeout=None,
            )
        )
        read, write, _session_id = await stack.enter_async_context(
            streamable_http_client(url, http_client=client)
        )
        return read, write

    def _stdio_environment(self) -> dict[str, str]:
        definition = self.server.definition
        environment = {
            key: _resolve_placeholders(value, self.server)
            for key, value in definition.env.items()
        }
        for name in definition.env_vars:
            value = os.environ.get(name)
            if value is not None:
                environment[name] = value
        for name in definition.required_env_vars:
            value = os.environ.get(name)
            if value is None:
                raise McpConfigurationError(
                    f"Environment variable {name!r} required by MCP server "
                    f"{self.server.name!r} is not set"
                )
            environment[name] = value
        for name, reference in definition.credential_env.items():
            environment[name] = self._credential(reference.connection_id)
        if self.server.plugin_root is not None and self.server.plugin_data is not None:
            environment["PLUGIN_ROOT"] = str(self.server.plugin_root)
            environment["PLUGIN_DATA"] = str(self.server.plugin_data)
        return environment

    def _http_headers(self) -> dict[str, str]:
        definition = self.server.definition
        headers = dict(definition.headers)
        for header, env_name in definition.env_http_headers.items():
            value = os.environ.get(env_name)
            if value is not None:
                _set_header(headers, header, value)
        token: str | None = None
        if definition.bearer_token_env_var is not None:
            token = os.environ.get(definition.bearer_token_env_var)
            if token is None:
                raise McpConfigurationError(
                    f"Environment variable {definition.bearer_token_env_var!r} "
                    f"required by MCP server {self.server.name!r} is not set"
                )
        elif definition.bearer_token_credential is not None:
            token = self._credential(definition.bearer_token_credential.connection_id)
        if token is not None:
            _set_header(headers, "Authorization", f"Bearer {token}")
        return headers

    def _http_url(self) -> str:
        definition = self.server.definition
        url = definition.url or ""
        if not definition.env_url_params:
            return url
        parsed = urlsplit(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        replaced = set(definition.env_url_params)
        query = [(name, value) for name, value in query if name not in replaced]
        for parameter, env_name in definition.env_url_params.items():
            value = os.environ.get(env_name)
            if value is None:
                raise McpConfigurationError(
                    f"Environment variable {env_name!r} required by MCP server "
                    f"{self.server.name!r} is not set"
                )
            query.append((parameter, value))
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    def _credential(self, connection_id: str) -> str:
        if self._credential_resolver is None:
            raise McpConfigurationError(
                f"MCP server {self.server.name!r} requests provider credential "
                f"{connection_id!r}, but no credential resolver is available"
            )
        value = self._credential_resolver(connection_id)
        if not value:
            raise McpConfigurationError(
                f"No credential is configured for DeepCode connection {connection_id!r}"
            )
        return value


async def _list_all_tools(session: Any) -> tuple[Any, ...]:
    tools: list[Any] = []
    cursor: str | None = None
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(page.tools)
        if len(tools) > MAX_DISCOVERED_TOOLS:
            raise McpConfigurationError(
                f"MCP server exposes more than {MAX_DISCOVERED_TOOLS} tools; "
                "configure an allow-list"
            )
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            return tuple(tools)


async def _count_all_pages(
    loader,
    *,
    attribute: str,
    limit: int,
    label: str,
) -> int:
    count = 0
    cursor: str | None = None
    while True:
        page = await loader(cursor=cursor)
        count += len(getattr(page, attribute, ()))
        if count > limit:
            raise McpConfigurationError(f"MCP server exposes more than {limit} {label}")
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            return count


def _resolve_path_or_command(value: str, server: ResolvedMcpServer) -> str:
    expanded = _resolve_placeholders(value, server)
    candidate = Path(expanded).expanduser()
    if not candidate.is_absolute() and ("/" in expanded or "\\" in expanded):
        candidate = server.config_dir / candidate
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        if server.plugin_root is not None and value.startswith("./"):
            _require_contained(resolved, server.plugin_root, label="command")
        # Preserve the executable token after using its resolved target for
        # containment checks. Resolving a Python virtualenv symlink here would
        # silently launch the base interpreter without the installed MCP SDK.
        return str(candidate)
    return expanded


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    """Apply a client-owned header with case-insensitive precedence."""

    for configured in tuple(headers):
        if configured.casefold() == name.casefold():
            headers.pop(configured)
    headers[name] = value


def _plugin_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Create an MCP client that never forwards Plugin headers by redirect."""

    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
    )


def _resolve_cwd(value: str | None, server: ResolvedMcpServer) -> str | None:
    if value is None:
        return None
    expanded = Path(_resolve_placeholders(value, server)).expanduser()
    candidate = expanded if expanded.is_absolute() else server.config_dir / expanded
    resolved = candidate.resolve(strict=False)
    if server.plugin_root is not None and server.plugin_data is not None:
        permitted = (
            server.plugin_data
            if value.startswith("${PLUGIN_DATA}")
            else server.plugin_root
        )
        _require_contained(resolved, permitted, label="cwd")
    return str(resolved)


def _require_contained(candidate: Path, base: Path, *, label: str) -> None:
    try:
        candidate.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise McpConfigurationError(
            f"Plugin MCP {label} escapes its permitted directory"
        ) from exc


def _resolve_placeholders(value: str, server: ResolvedMcpServer) -> str:
    replacements = {
        "workspace": str(server.workspace),
        "configDir": str(server.config_dir),
    }
    if server.plugin_root is not None and server.plugin_data is not None:
        replacements.update(
            {
                "PLUGIN_ROOT": str(server.plugin_root),
                "PLUGIN_DATA": str(server.plugin_data),
            }
        )

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in replacements and server.source.value == "plugin":
            return match.group(0)
        if name not in replacements:
            raise McpConfigurationError(
                f"Unsupported MCP placeholder ${{{name}}}; use envVars for "
                "environment forwarding"
            )
        return replacements[name]

    return _PLACEHOLDER.sub(replace, value)


def _bounded_instructions(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:4_000]


def _open_stderr_log(server: ResolvedMcpServer):
    try:
        configured_home = os.environ.get("DEEPCODE_HOME")
        home = (
            Path(configured_home).expanduser()
            if configured_home
            else Path.home() / ".deepcode"
        )
        target = home / "logs" / "mcp"
        target.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", server.server_id)
        return (target / f"{safe}.log").open("a", encoding="utf-8", buffering=1)
    except OSError:
        return None


__all__ = [
    "MAX_DISCOVERED_TOOLS",
    "CredentialResolver",
    "McpConnection",
    "OAuthProviderFactory",
]
