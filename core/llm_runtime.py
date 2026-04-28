"""Workflow-facing LLM helpers.

This module is intentionally thin: provider construction still belongs to
``core.config`` / ``core.compat.runtime``. Workflow code should use this layer
so phase selection, logging, and future per-session overrides stay in one
place instead of being reimplemented in every agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from core.compat.runtime import get_runtime
from core.providers.base import LLMProvider

if TYPE_CHECKING:
    from core.compat import Agent, AugmentedLLM


@dataclass(frozen=True, slots=True)
class LLMProfile:
    """Resolved LLM selection for one workflow call."""

    provider_name: str
    phase: str
    model: str
    reasoning_effort: str | None
    max_tokens: int


def get_workflow_provider(
    *,
    phase: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> tuple[LLMProvider, LLMProfile]:
    """Resolve a provider for non-AgentRunner workflow code.

    Prefer :func:`attach_workflow_llm` for normal agents. This function exists
    for legacy loops that still manage tool execution manually but should not
    instantiate OpenAI/Anthropic/Google SDK clients themselves.
    """
    runtime = get_runtime()
    provider = runtime.provider_for(
        provider_name=provider_name,
        phase=phase,
        model=model,
    )
    resolved_provider = (
        provider_name
        or runtime.config.get_provider_name(model)
        or runtime.config.llm_provider
        or "auto"
    ).lower()
    profile = LLMProfile(
        provider_name=resolved_provider,
        phase=phase,
        model=provider.get_default_model(),
        reasoning_effort=provider.generation.reasoning_effort,
        max_tokens=provider.generation.max_tokens,
    )
    logger.info(
        "Resolved workflow LLM: phase={} provider={} model={} reasoning_effort={} max_tokens={}",
        profile.phase,
        profile.provider_name,
        profile.model,
        profile.reasoning_effort,
        profile.max_tokens,
    )
    return provider, profile


async def attach_workflow_llm(
    agent: "Agent",
    *,
    phase: str,
    provider_name: str | None = None,
    model: str | None = None,
) -> "AugmentedLLM":
    """Attach an LLM to an agent with explicit workflow phase semantics."""
    llm = await agent.attach_llm(
        phase=phase,
        provider_name=provider_name,
        model=model,
    )
    logger.info(
        "Attached workflow LLM: agent={} phase={} provider={} model={} reasoning_effort={}",
        agent.name,
        phase,
        llm.provider_name,
        llm.provider.get_default_model(),
        llm.provider.generation.reasoning_effort,
    )
    return llm
