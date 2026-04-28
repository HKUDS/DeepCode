"""MCP client: connects to MCP servers and wraps their tools as native tools.

Ported from nanobot.agent.tools.mcp. Compared to the upstream version we:

- Accept a small ``MCPServerConfig`` dataclass (this module) so callers can
  feed in entries materialised from ``deepcode_config.json`` without taking
  a Pydantic dependency at the agent-runtime layer.
- Track per-server :class:`AsyncExitStack` on the supplied
  :class:`ToolRegistry` so the orchestration code can close them all with a
  single ``await tool_registry.aclose()`` call.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from core.agent_runtime.tools.base import Tool
from core.agent_runtime.tools.registry import ToolRegistry
from core.observability import current_task_id, log_mcp_call
from core.platform_compat import normalize_stdio_command


@dataclass(slots=True)
class MCPServerConfig:
    """Subset of nanobot's MCP server config sufficient for DeepCode's needs."""

    name: str
    type: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    enabled_tools: list[str] = field(default_factory=lambda: ["*"])
    tool_timeout: int = 300
    description: str | None = None


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is not None:
            branch, _ = nullable_branch
            merged = {k: v for k, v in normalized.items() if k != key}
            merged.update(branch)
            normalized = merged
            normalized["nullable"] = True
            break

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in normalized["properties"].items()
        }

    if "items" in normalized and isinstance(normalized["items"], dict):
        normalized["items"] = _normalize_schema_for_openai(normalized["items"])

    if normalized.get("type") != "object":
        return normalized

    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a native Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 300):
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        raw_schema = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._parameters = _normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        # Parse "mcp_<server>_<tool>" back into its components for logging.
        # Falls back to the wrapped name when the convention is broken.
        server_name = self._server_name(self._name)
        started = time.monotonic()
        status = "ok"
        error_msg: str | None = None
        result_text: str = ""
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            status = "error"
            error_msg = f"timeout after {self._tool_timeout}s"
            self._record_observability(server_name, kwargs, error_msg, status, started)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
            status = "error"
            error_msg = "cancelled"
            self._record_observability(server_name, kwargs, error_msg, status, started)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.exception(
                "MCP tool '{}' failed: {}: {}",
                self._name,
                type(exc).__name__,
                exc,
            )
            status = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            self._record_observability(server_name, kwargs, error_msg, status, started)
            return f"(MCP tool call failed: {type(exc).__name__})"

        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        result_text = "\n".join(parts) or "(no output)"
        self._record_observability(server_name, kwargs, result_text, status, started)
        return result_text

    def _server_name(self, wrapped: str) -> str:
        """Recover the originating MCP server name from ``mcp_<server>_<tool>``."""
        prefix = "mcp_"
        if not wrapped.startswith(prefix):
            return wrapped
        rest = wrapped[len(prefix):]
        suffix = "_" + self._original_name
        if rest.endswith(suffix):
            return rest[: -len(suffix)] or wrapped
        return rest.split("_", 1)[0]

    def _record_observability(
        self,
        server: str,
        arguments: dict[str, Any],
        result_or_error: Any,
        status: str,
        started_at: float,
    ) -> None:
        """Forward one MCP call to the observability bus (errors swallowed)."""
        try:
            log_mcp_call(
                server=server,
                tool=self._original_name,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                status=status,
                arguments=arguments,
                result=result_or_error if status == "ok" else None,
                error=result_or_error if status != "ok" else None,
            )
        except Exception:  # pragma: no cover
            pass


class MCPResourceWrapper(Tool):
    """Wraps an MCP resource URI as a read-only Tool."""

    def __init__(self, session, server_name: str, resource_def, resource_timeout: int = 300):
        self._session = session
        self._uri = resource_def.uri
        self._name = f"mcp_{server_name}_resource_{resource_def.name}"
        desc = resource_def.description or resource_def.name
        self._description = f"[MCP Resource] {desc}\nURI: {self._uri}"
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._resource_timeout = resource_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.read_resource(self._uri),
                timeout=self._resource_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MCP resource '{}' timed out after {}s", self._name, self._resource_timeout
            )
            return f"(MCP resource read timed out after {self._resource_timeout}s)"
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP resource '{}' was cancelled by server/SDK", self._name)
            return "(MCP resource read was cancelled)"
        except Exception as exc:
            logger.exception(
                "MCP resource '{}' failed: {}: {}",
                self._name,
                type(exc).__name__,
                exc,
            )
            return f"(MCP resource read failed: {type(exc).__name__})"

        parts: list[str] = []
        for block in result.contents:
            if isinstance(block, types.TextResourceContents):
                parts.append(block.text)
            elif isinstance(block, types.BlobResourceContents):
                parts.append(f"[Binary resource: {len(block.blob)} bytes]")
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


class MCPPromptWrapper(Tool):
    """Wraps an MCP prompt as a read-only Tool."""

    def __init__(self, session, server_name: str, prompt_def, prompt_timeout: int = 300):
        self._session = session
        self._prompt_name = prompt_def.name
        self._name = f"mcp_{server_name}_prompt_{prompt_def.name}"
        desc = prompt_def.description or prompt_def.name
        self._description = (
            f"[MCP Prompt] {desc}\n"
            "Returns a filled prompt template that can be used as a workflow guide."
        )
        self._prompt_timeout = prompt_timeout

        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in prompt_def.arguments or []:
            prop: dict[str, Any] = {"type": "string"}
            if getattr(arg, "description", None):
                prop["description"] = arg.description
            properties[arg.name] = prop
            if arg.required:
                required.append(arg.name)
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types
        from mcp.shared.exceptions import McpError

        try:
            result = await asyncio.wait_for(
                self._session.get_prompt(self._prompt_name, arguments=kwargs),
                timeout=self._prompt_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP prompt '{}' timed out after {}s", self._name, self._prompt_timeout)
            return f"(MCP prompt call timed out after {self._prompt_timeout}s)"
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP prompt '{}' was cancelled by server/SDK", self._name)
            return "(MCP prompt call was cancelled)"
        except McpError as exc:
            logger.error(
                "MCP prompt '{}' failed: code={} message={}",
                self._name,
                exc.error.code,
                exc.error.message,
            )
            return f"(MCP prompt call failed: {exc.error.message} [code {exc.error.code}])"
        except Exception as exc:
            logger.exception(
                "MCP prompt '{}' failed: {}: {}",
                self._name,
                type(exc).__name__,
                exc,
            )
            return f"(MCP prompt call failed: {type(exc).__name__})"

        parts: list[str] = []
        for message in result.messages:
            content = message.content
            if isinstance(content, types.TextContent):
                parts.append(content.text)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, types.TextContent):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
            else:
                parts.append(str(content))
        return "\n".join(parts) or "(no output)"


def _open_mcp_stderr_log(server_name: str, server_stack: AsyncExitStack):
    """Open ``mcp_server_<name>.log`` in the active task's logs dir.

    Falls back to ``logs/mcp_servers/<name>.log`` when there is no active
    task (e.g. process-startup smoke test). The file handle is registered
    with ``server_stack`` so it is closed when the MCP connection tears
    down. Returns ``None`` on any error so the caller can use the SDK's
    default stderr instead.
    """
    try:
        # Resolve task dir lazily to avoid a hard dependency cycle: bus.py
        # imports records.py only, not this module.
        from core.observability.bus import _resolve_task_dir  # type: ignore

        task_id = current_task_id()
        target_dir: Path
        if task_id and (task_dir := _resolve_task_dir(task_id)) is not None:
            target_dir = task_dir / "logs"
        else:
            target_dir = Path("logs") / "mcp_servers"
        target_dir.mkdir(parents=True, exist_ok=True)

        path = target_dir / f"mcp_server_{server_name}.log"
        # Append mode keeps history across multiple runs of the same task.
        handle = path.open("a", encoding="utf-8", buffering=1)
        server_stack.callback(handle.close)
        return handle
    except Exception:
        return None


async def connect_mcp_servers(
    mcp_servers: dict[str, MCPServerConfig],
    registry: ToolRegistry,
) -> dict[str, AsyncExitStack]:
    """Connect to configured MCP servers and register their tools/resources/prompts.

    Each server gets its own ``AsyncExitStack`` and is also registered with the
    ``ToolRegistry`` (via :meth:`ToolRegistry.attach_server_stack`) so that a
    single ``await registry.aclose()`` later tears down all stdio child
    processes.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client

    try:
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client
    except ImportError:  # pragma: no cover - some mcp versions use the snake_case name
        from mcp.client.streamable_http import streamable_http_client  # type: ignore[no-redef]

    async def connect_single_server(
        name: str, cfg: MCPServerConfig
    ) -> tuple[str, AsyncExitStack | None]:
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()

        try:
            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"
                elif cfg.url:
                    transport_type = (
                        "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
                    )
                else:
                    logger.warning("MCP server '{}': no command or url configured, skipping", name)
                    await server_stack.aclose()
                    return name, None

            if transport_type == "stdio":
                command, args, env = normalize_stdio_command(
                    cfg.command or "",
                    cfg.args,
                    cfg.env or None,
                )
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                )
                # Redirect the child's stderr to a per-task (or per-process)
                # file so we can inspect MCP server crashes after the fact
                # rather than relying on console output. Failures here are
                # non-fatal: we fall back to the SDK's default stderr.
                errlog = _open_mcp_stderr_log(name, server_stack)
                if errlog is not None:
                    read, write = await server_stack.enter_async_context(
                        stdio_client(params, errlog=errlog)
                    )
                else:
                    read, write = await server_stack.enter_async_context(stdio_client(params))
            elif transport_type == "sse":

                def httpx_client_factory(
                    headers: dict[str, str] | None = None,
                    timeout: httpx.Timeout | None = None,
                    auth: httpx.Auth | None = None,
                ) -> httpx.AsyncClient:
                    merged_headers = {
                        "Accept": "application/json, text/event-stream",
                        **(cfg.headers or {}),
                        **(headers or {}),
                    }
                    return httpx.AsyncClient(
                        headers=merged_headers or None,
                        follow_redirects=True,
                        timeout=timeout,
                        auth=auth,
                    )

                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
                )
            elif transport_type == "streamableHttp":
                http_client = await server_stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.headers or None,
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await server_stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                await server_stack.aclose()
                return name, None

            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            registered_count = 0
            matched_enabled_tools: set[str] = set()
            available_raw_names = [tool_def.name for tool_def in tools.tools]
            available_wrapped_names = [f"mcp_{name}_{tool_def.name}" for tool_def in tools.tools]
            for tool_def in tools.tools:
                wrapped_name = f"mcp_{name}_{tool_def.name}"
                if (
                    not allow_all_tools
                    and tool_def.name not in enabled_tools
                    and wrapped_name not in enabled_tools
                ):
                    logger.debug(
                        "MCP: skipping tool '{}' from server '{}' (not in enabledTools)",
                        wrapped_name,
                        name,
                    )
                    continue
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                registry.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                registered_count += 1
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            if enabled_tools and not allow_all_tools:
                unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
                if unmatched_enabled_tools:
                    logger.warning(
                        "MCP server '{}': enabledTools entries not found: {}. Available raw names: {}. "
                        "Available wrapped names: {}",
                        name,
                        ", ".join(unmatched_enabled_tools),
                        ", ".join(available_raw_names) or "(none)",
                        ", ".join(available_wrapped_names) or "(none)",
                    )

            try:
                resources_result = await session.list_resources()
                for resource in resources_result.resources:
                    wrapper = MCPResourceWrapper(
                        session, name, resource, resource_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
                    registered_count += 1
                    logger.debug(
                        "MCP: registered resource '{}' from server '{}'", wrapper.name, name
                    )
            except Exception as e:
                logger.debug("MCP server '{}': resources not supported or failed: {}", name, e)

            try:
                prompts_result = await session.list_prompts()
                for prompt in prompts_result.prompts:
                    wrapper = MCPPromptWrapper(
                        session, name, prompt, prompt_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
                    registered_count += 1
                    logger.debug("MCP: registered prompt '{}' from server '{}'", wrapper.name, name)
            except Exception as e:
                logger.debug("MCP server '{}': prompts not supported or failed: {}", name, e)

            logger.info(
                "MCP server '{}': connected, {} capabilities registered", name, registered_count
            )
            return name, server_stack

        except Exception as e:
            hint = ""
            text = str(e).lower()
            if any(
                marker in text
                for marker in (
                    "parse error",
                    "invalid json",
                    "unexpected token",
                    "jsonrpc",
                    "content-length",
                )
            ):
                hint = (
                    " Hint: this looks like stdio protocol pollution. Make sure the MCP server writes "
                    "only JSON-RPC to stdout and sends logs/debug output to stderr instead."
                )
            logger.error("MCP server '{}': failed to connect: {}{}", name, e, hint)
            try:
                await server_stack.aclose()
            except Exception:
                pass
            return name, None

    server_stacks: dict[str, AsyncExitStack] = {}

    # NOTE: We deliberately connect servers **sequentially** rather than via
    # ``asyncio.gather(*[create_task(...)])``. The MCP stdio_client uses
    # ``anyio`` cancel scopes that are pinned to the task in which they were
    # entered; if each server is entered inside a different child task,
    # closing the stack later from the parent task triggers
    # ``RuntimeError: Attempted to exit cancel scope in a different task
    # than it was entered in``. Sequential connect keeps every stack's
    # enter/exit on the same task and is fast enough in practice (DeepCode
    # agents typically use 1-3 MCP servers).
    for name, cfg in mcp_servers.items():
        try:
            srv_name, srv_stack = await connect_single_server(name, cfg)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            logger.error("MCP server '{}' connection failed: {}", name, exc)
            continue
        if srv_stack is not None:
            server_stacks[srv_name] = srv_stack
            registry.attach_server_stack(srv_name, srv_stack)

    return server_stacks
