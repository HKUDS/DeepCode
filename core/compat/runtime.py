"""Process-wide runtime singleton for the DeepCode compatibility layer.

The legacy code obtained logger / config / MCP wiring from
``mcp_agent``'s :class:`MCPApp` async context manager. We replace it with a
single :class:`DeepCodeRuntime` object that owns:

- the merged :class:`core.config.DeepCodeConfig`
- a loguru logger (DeepCode already standardised on loguru)
- a lazily-instantiated :class:`core.providers.base.LLMProvider` cache,
  keyed by ``(provider_name, phase, model)``

Each :class:`core.compat.MCPApp` instance pulls a runtime from this module
on entry and releases it on exit, so the agent / orchestration code can
keep using a process-wide ``app.context.config.mcp.servers`` namespace
while we incrementally tear ``mcp_agent`` out of the codebase.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.config import (
    DeepCodeConfig,
    LLMProviderConfig,
    load_config,
    make_llm_provider,
)
from core.providers.base import LLMProvider


_runtime_lock = threading.Lock()
_runtime: "DeepCodeRuntime | None" = None


@dataclass(slots=True)
class _MCPNamespace:
    """Mirror of ``mcp_agent.config.mcp`` exposing ``.servers`` only."""

    servers: dict[str, Any]


@dataclass(slots=True)
class _ConfigNamespace:
    """Mirror of ``mcp_agent.context.config`` exposing the bits DeepCode reads."""

    mcp: _MCPNamespace
    raw: dict[str, Any]


@dataclass(slots=True)
class _ContextNamespace:
    """Mirror of ``mcp_agent.context``."""

    config: _ConfigNamespace


class DeepCodeRuntime:
    """Owns the loaded DeepCode config + provider cache for one process."""

    def __init__(self, config: DeepCodeConfig) -> None:
        self.config = config
        self.logger = logger
        self._provider_cache: dict[tuple[str, str, str | None], LLMProvider] = {}
        mcp_namespace = _MCPNamespace(servers=config.mcp_servers)
        config_namespace = _ConfigNamespace(mcp=mcp_namespace, raw=config.raw)
        self.context = _ContextNamespace(config=config_namespace)

    @classmethod
    def load(
        cls,
        config_path: str | None = None,
        secrets_path: str | None = None,
    ) -> "DeepCodeRuntime":
        """Read the YAML files and build a fresh :class:`DeepCodeRuntime`."""
        cfg = load_config(config_path=config_path, secrets_path=secrets_path)
        return cls(cfg)

    def provider_for(
        self,
        *,
        provider_name: str | None = None,
        phase: str = "default",
        model: str | None = None,
    ) -> LLMProvider:
        """Return a cached :class:`LLMProvider` for the requested combination."""
        chosen_provider = (provider_name or self.config.llm_provider or "openai").lower()
        if chosen_provider == "google":
            chosen_provider = "gemini"
        cache_key = (chosen_provider, phase, model)
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]
        provider = make_llm_provider(
            self.config,
            model=model,
            provider_name=chosen_provider,
            phase=phase,
        )
        self._provider_cache[cache_key] = provider
        return provider

    def get_provider_config(self, name: str | None = None) -> LLMProviderConfig:
        """Return the configured :class:`LLMProviderConfig` for ``name``."""
        return self.config.get_provider_config(name)


def get_runtime() -> DeepCodeRuntime:
    """Return the process-wide runtime, loading lazily if needed."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = DeepCodeRuntime.load()
    return _runtime


def set_runtime(runtime: DeepCodeRuntime | None) -> None:
    """Override the process-wide runtime (useful for tests / reloads)."""
    global _runtime
    with _runtime_lock:
        _runtime = runtime
