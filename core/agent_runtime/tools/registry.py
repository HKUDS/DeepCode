"""Tool registry for dynamic tool management (ported from nanobot)."""

from contextlib import AsyncExitStack
from typing import Any

from core.agent_runtime.tools.base import Tool


class ToolRegistry:
    """Registry for agent tools with lazy MCP-server lifecycle ownership.

    Owns an optional ``AsyncExitStack`` so the high-level orchestration code
    can ``await registry.aclose()`` to tear down all stdio MCP processes the
    registry holds (populated by :func:`connect_mcp_servers`).
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        self._exit_stack: AsyncExitStack = AsyncExitStack()
        self._owned_server_stacks: dict[str, AsyncExitStack] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        if not isinstance(params, dict) and name in ('write_file', 'read_file'):
            return None, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got {type(params).__name__}. "
                "Use named parameters: tool_name(param1=\"value1\", param2=\"value2\")"
            )

        tool = self._tools.get(name)
        if not tool:
            return None, params, (
                f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        _HINT = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + _HINT

        try:
            assert tool is not None
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def attach_server_stack(self, server_name: str, stack: AsyncExitStack) -> None:
        """Track a per-MCP-server :class:`AsyncExitStack` so :meth:`aclose` can drain it."""
        self._owned_server_stacks[server_name] = stack

    async def aclose(self) -> None:
        """Close every owned MCP server stack and forget all tools."""
        errors: list[BaseException] = []
        for name, stack in list(self._owned_server_stacks.items()):
            try:
                await stack.aclose()
            except BaseException as exc:  # noqa: BLE001 - log and continue
                errors.append(exc)
            finally:
                self._owned_server_stacks.pop(name, None)
        try:
            await self._exit_stack.aclose()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        self._tools.clear()
        self._cached_definitions = None
        if errors:
            from loguru import logger
            for exc in errors:
                # MCP stdio_client uses anyio cancel scopes that are isolated
                # to the supervisor task (see core/compat/agent.py). When that
                # supervisor is cancelled, anyio's stack teardown surfaces a
                # benign CancelledError / "exit cancel scope in a different
                # task" message — it does not affect the caller. Treat it as
                # DEBUG noise so logs only show genuine close failures.
                if _is_benign_cancel_teardown(exc):
                    logger.debug(
                        "ToolRegistry.aclose: benign anyio teardown noise: {}", exc
                    )
                else:
                    logger.warning(
                        "ToolRegistry.aclose: error draining stack: {}", exc
                    )


def _is_benign_cancel_teardown(exc: BaseException) -> bool:
    """Return ``True`` if *exc* is the well-known anyio cancel-scope churn
    that fires during supervisor-task shutdown.

    These messages fire when the supervisor task wrapping the MCP stdio
    sessions is cancelled and anyio unwinds its task-scoped cancel stack.
    The caller pipeline is unaffected, so we don't want WARNING noise.
    """
    import asyncio
    if isinstance(exc, asyncio.CancelledError):
        return True
    msg = str(exc).lower()
    benign_markers = (
        "cancel scope in a different task",
        "isn't the current task",
        "cancelled via cancel scope",
    )
    return any(marker in msg for marker in benign_markers)
