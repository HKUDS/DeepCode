"""DeepCode core: nanobot-style LLM stack and agent runtime.

Replaces the legacy ``mcp_agent`` based pipeline. Public surface lives under
``core.providers`` (LLM SDK wrappers), ``core.agent_runtime`` (agent loop +
tools + MCP client), and ``core.config`` (yaml -> provider/registry wiring).
"""
