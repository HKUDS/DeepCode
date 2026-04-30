"""Tools subsystem: native :class:`Tool` ABC + registry + MCP wrappers."""

from core.agent_runtime.tools.base import Schema, Tool, tool_parameters
from core.agent_runtime.tools.mcp import (
    MCPPromptWrapper,
    MCPResourceWrapper,
    MCPToolWrapper,
    connect_mcp_servers,
)
from core.agent_runtime.tools.registry import ToolRegistry

__all__ = [
    "MCPPromptWrapper",
    "MCPResourceWrapper",
    "MCPToolWrapper",
    "Schema",
    "Tool",
    "ToolRegistry",
    "connect_mcp_servers",
    "tool_parameters",
]
