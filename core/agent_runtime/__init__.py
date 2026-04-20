"""Agent runtime: stateless tool-using LLM loop ported from nanobot.

Public surface:
- :class:`AgentRunner`, :class:`AgentRunSpec`, :class:`AgentRunResult`
  (:mod:`core.agent_runtime.runner`)
- :class:`AgentHook`, :class:`AgentHookContext`, :class:`CompositeHook`
  (:mod:`core.agent_runtime.hook`)
- :class:`Tool`, :class:`ToolRegistry`, ``connect_mcp_servers``
  (:mod:`core.agent_runtime.tools`)
- :func:`run_parallel_llm` (:mod:`core.agent_runtime.parallel`)
"""

from core.agent_runtime.hook import AgentHook, AgentHookContext, CompositeHook
from core.agent_runtime.runner import AgentRunner, AgentRunResult, AgentRunSpec
from core.agent_runtime.tools.base import Tool
from core.agent_runtime.tools.mcp import (
    MCPPromptWrapper,
    MCPResourceWrapper,
    MCPToolWrapper,
    connect_mcp_servers,
)
from core.agent_runtime.tools.registry import ToolRegistry

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentRunner",
    "AgentRunResult",
    "AgentRunSpec",
    "CompositeHook",
    "MCPPromptWrapper",
    "MCPResourceWrapper",
    "MCPToolWrapper",
    "Tool",
    "ToolRegistry",
    "connect_mcp_servers",
]
