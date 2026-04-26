"""Workflow-facing LLM helpers.

This module is intentionally thin: provider construction still belongs to
``core.config`` / ``core.compat.runtime``. Workflow code should use this layer
so phase selection, logging, and future per-session overrides stay in one
place instead of being reimplemented in every agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from core.compat.runtime import DeepCodeRuntime, get_runtime
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


def _runtime_from_legacy_secrets_path(secrets_path: str | Path | None) -> DeepCodeRuntime:
    """Return a runtime, honoring legacy workflow ``config_path`` arguments.

    Existing implementation workflows pass ``mcp_agent.secrets.yaml`` around as
    ``config_path``. The new provider path can still respect that by deriving
    the sibling ``mcp_agent.config.yaml`` when a path is explicitly supplied.
    """
    if not secrets_path:
        return get_runtime()

    secrets = Path(secrets_path).expanduser().resolve()
    config = secrets.parent / "mcp_agent.config.yaml"
    if not secrets.exists() and not config.exists():
        return get_runtime()
    return DeepCodeRuntime.load(
        config_path=str(config) if config.exists() else None,
        secrets_path=str(secrets) if secrets.exists() else None,
    )


def get_workflow_provider(
    *,
    phase: str,
    provider_name: str | None = None,
    model: str | None = None,
    legacy_secrets_path: str | Path | None = None,
) -> tuple[LLMProvider, LLMProfile]:
    """Resolve a provider for non-AgentRunner workflow code.

    Prefer ``attach_workflow_llm`` for normal agents. This function exists for
    legacy loops that still manage tool execution manually but should no longer
    instantiate OpenAI/Anthropic/Google SDK clients themselves.
    """
    runtime = _runtime_from_legacy_secrets_path(legacy_secrets_path)
    provider = runtime.provider_for(
        provider_name=provider_name,
        phase=phase,
        model=model,
    )
    resolved_provider = (provider_name or runtime.config.llm_provider or "openai").lower()
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
