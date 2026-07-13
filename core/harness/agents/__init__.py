"""Model-driven concurrent sub-agent delegation (C2).

Replaces the retired deterministic ``deepcode team`` pipeline: the model itself
spawns sub-agents that run concurrently, and their results flow back through a
mailbox. See :class:`~core.harness.agents.control.AgentControl`.
"""

from core.harness.agents.control import AgentControl, AgentLimitError, SubAgent

__all__ = ["AgentControl", "AgentLimitError", "SubAgent"]
