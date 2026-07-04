"""Expose registered tools under alternate (usually bare) names.

MCP tools register as ``mcp_<server>_<tool>`` to avoid cross-server
collisions, but prompts and legacy workflows address tools by their bare
server-side names (``write_file``, ``read_code_mem``, ...). This module
lets a workflow build a model-facing registry that re-exposes selected
tools under those bare names while the underlying registry keeps owning
the MCP server lifecycle.
"""

from __future__ import annotations

from typing import Any

from core.agent_runtime.tools.base import Tool
from core.agent_runtime.tools.registry import ToolRegistry


class AliasedTool(Tool):
    """A tool re-exposed under a different name; delegates everything else."""

    def __init__(self, inner: Tool, alias: str):
        self._inner = inner
        self._alias = alias

    @property
    def inner(self) -> Tool:
        return self._inner

    @property
    def name(self) -> str:
        return self._alias

    @property
    def description(self) -> str:
        return self._inner.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._inner.parameters

    @property
    def read_only(self) -> bool:
        return self._inner.read_only

    @property
    def exclusive(self) -> bool:
        return self._inner.exclusive

    @property
    def concurrency_safe(self) -> bool:
        return self._inner.concurrency_safe

    async def execute(self, **kwargs: Any) -> Any:
        return await self._inner.execute(**kwargs)


def find_tool_by_suffix(registry: ToolRegistry, bare_name: str) -> Tool | None:
    """Locate a tool by exact name, then by ``*_<bare_name>`` suffix.

    Mirrors the lookup that ``core.compat.Agent.call_tool`` performs so
    bare names resolve to whichever MCP server provides them.
    """
    tool = registry.get(bare_name)
    if tool is not None:
        return tool
    suffix = f"_{bare_name}"
    for candidate in registry.tool_names:
        if candidate.endswith(suffix):
            return registry.get(candidate)
    return None


def build_aliased_registry(
    source: ToolRegistry,
    bare_names: list[str],
) -> tuple[ToolRegistry, list[str]]:
    """Build a registry exposing ``bare_names`` aliased from ``source``.

    Returns the new registry plus the bare names that could not be
    resolved (e.g. because their MCP server failed to start). The
    returned registry owns no server stacks — closing it is a no-op and
    the source registry remains responsible for MCP process cleanup.
    """
    aliased = ToolRegistry()
    missing: list[str] = []
    for bare_name in bare_names:
        tool = find_tool_by_suffix(source, bare_name)
        if tool is None:
            missing.append(bare_name)
            continue
        if tool.name == bare_name:
            aliased.register(tool)
        else:
            aliased.register(AliasedTool(tool, bare_name))
    return aliased, missing
