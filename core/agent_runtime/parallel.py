"""Public ``run_parallel_llm`` entry point (see ``core.agent_runtime.__init__``).

The fan-out / fan-in machinery lives in :class:`core.compat.parallel.ParallelLLM`
(a drop-in for the legacy ``mcp_agent`` helper). This module only exposes the
documented, ergonomic entry point so the name ``run_parallel_llm`` actually
resolves: build the :class:`~core.compat.agent.Agent` participants, hand them to
:class:`ParallelLLM`, and reduce the branch outputs through the fan-in agent.

Typical use — one prompt fanned out to N independent workers, then aggregated::

    from core.agent_runtime.parallel import run_parallel_llm
    from core.compat.agent import Agent

    workers = [Agent(name=f"worker-{i}", instruction="...") for i in range(3)]
    fan_in = Agent(name="aggregator", instruction="Synthesize the workers.")
    result = await run_parallel_llm(
        "Summarize the tradeoffs of X", fan_in_agent=fan_in, fan_out_agents=workers
    )

Each worker branch runs concurrently (``asyncio.gather``); a branch that raises
is logged and folded in as ``[<name> error: ...]`` rather than aborting the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence, Type

if TYPE_CHECKING:
    from core.compat.agent import Agent, AugmentedLLM
    from core.compat.request_params import RequestParams

__all__ = ["run_parallel_llm"]


async def run_parallel_llm(
    message: str,
    *,
    fan_in_agent: Agent,
    fan_out_agents: Sequence[Agent] | Iterable[Agent],
    llm_factory: Callable[[Agent], Any] | Type[AugmentedLLM] | None = None,
    request_params: RequestParams | None = None,
) -> str:
    """Fan ``message`` out to ``fan_out_agents`` concurrently and aggregate via ``fan_in_agent``.

    Thin wrapper over :meth:`core.compat.parallel.ParallelLLM.generate_str`. The
    named keyword arguments mirror :class:`ParallelLLM`'s constructor so the
    call site reads as "run this prompt in parallel"; the return value is the
    fan-in agent's synthesized answer.

    Args:
        message: The prompt sent to every fan-out worker.
        fan_in_agent: Agent that reduces the worker outputs into one answer.
        fan_out_agents: The workers that each answer ``message`` concurrently.
        llm_factory: Optional factory/marker used to attach an LLM to each
            agent (see :meth:`ParallelLLM._attach`). ``None`` uses each agent's
            default provider.
        request_params: Optional request parameters forwarded to every branch
            and to the fan-in call.

    Returns:
        The fan-in agent's aggregated text.
    """
    # Imported lazily: core.compat.parallel transitively pulls core.compat.agent
    # -> core.compat.runtime -> core.config, while core.config imports this
    # package's core.agent_runtime.tools.mcp at module load. A module-level
    # import here would close that cycle; deferring to call time keeps it open.
    from core.compat.parallel import ParallelLLM

    parallel = ParallelLLM(
        fan_in_agent=fan_in_agent,
        fan_out_agents=fan_out_agents,
        llm_factory=llm_factory,
    )
    return await parallel.generate_str(message, request_params)
