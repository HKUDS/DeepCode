"""Drop-in replacement for ``mcp_agent.workflows.parallel.parallel_llm.ParallelLLM``.

The legacy class fans a prompt out to N agents and reduces the responses
through a single fan-in agent. We reproduce just the behaviour DeepCode
relies on: ``await parallel.generate_str(message, request_params)``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable, Type

from loguru import logger

from core.compat.agent import Agent, AugmentedLLM
from core.compat.request_params import RequestParams


class ParallelLLM:
    """Fan-out / fan-in helper that mirrors the legacy ``ParallelLLM``."""

    def __init__(
        self,
        fan_in_agent: Agent,
        fan_out_agents: Iterable[Agent],
        llm_factory: Callable[[Agent], Any] | Type[AugmentedLLM] | None = None,
        instruction: str | None = None,
    ) -> None:
        self.fan_in_agent = fan_in_agent
        self.fan_out_agents: list[Agent] = list(fan_out_agents)
        self.llm_factory = llm_factory
        self.instruction = instruction or fan_in_agent.instruction

    async def _attach(self, agent: Agent) -> AugmentedLLM:
        if isinstance(self.llm_factory, type):
            return await agent.attach_llm(self.llm_factory)  # type: ignore[arg-type]
        if callable(self.llm_factory):
            maybe = self.llm_factory(agent)
            if asyncio.iscoroutine(maybe):
                return await maybe  # type: ignore[no-any-return]
            return maybe
        return await agent.attach_llm()

    async def generate_str(
        self,
        message: str,
        request_params: RequestParams | None = None,
    ) -> str:
        """Run all fan-out agents in parallel, then aggregate via fan-in."""

        async def _run_branch(agent: Agent) -> tuple[str, str]:
            async with agent:
                llm = await self._attach(agent)
                try:
                    text = await llm.generate_str(
                        message=message, request_params=request_params
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ParallelLLM branch '{}' failed: {}: {}",
                        agent.name,
                        type(exc).__name__,
                        exc,
                    )
                    return agent.name, f"[{agent.name} error: {exc}]"
                return agent.name, text or ""

        if not self.fan_out_agents:
            branch_results: list[tuple[str, str]] = []
        else:
            branch_results = await asyncio.gather(
                *(_run_branch(agent) for agent in self.fan_out_agents)
            )

        joined_branches = "\n\n".join(
            f"## {name}\n{text}" for name, text in branch_results
        )

        aggregator_message = (
            f"{message}\n\n---\n# Worker outputs\n{joined_branches}"
            if joined_branches
            else message
        )

        async with self.fan_in_agent:
            llm = await self._attach(self.fan_in_agent)
            return await llm.generate_str(
                message=aggregator_message,
                request_params=request_params,
            )
