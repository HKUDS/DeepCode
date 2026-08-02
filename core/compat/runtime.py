"""Process-wide runtime singleton for the DeepCode compatibility layer.

The legacy code obtained logger / config / MCP wiring from
``mcp_agent``'s :class:`MCPApp` async context manager. We replace it with a
single :class:`DeepCodeRuntime` object that owns:

- the parsed :class:`core.config.DeepCodeConfig`
- a loguru logger (DeepCode standardised on loguru)
- a lazily-instantiated :class:`core.providers.base.LLMProvider` cache,
  keyed by ``(provider_name, phase, model)``

Each :class:`core.compat.MCPApp` instance pulls a runtime from this module
on entry and releases it on exit, so the agent / orchestration code can
keep using a process-wide ``app.context.config.mcp.servers`` namespace
even though the underlying loader is now nanobot-style JSON.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from loguru import logger

from core.agent_runtime.tools.mcp import MCPServerConfig
from core.config import (
    DeepCodeConfig,
    ProviderConfig,
    load_config,
    make_llm_provider,
)
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.providers.base import LLMProvider
from core.providers.catalog_service import ModelCatalogService
from core.providers.credentials import CredentialStore
from core.providers.profiles import ConnectionResolver


_runtime_lock = threading.Lock()
_runtime: "DeepCodeRuntime | None" = None


@dataclass(slots=True)
class _MCPNamespace:
    """Mirror of ``mcp_agent.config.mcp`` exposing ``.servers`` only."""

    servers: dict[str, MCPServerConfig]


@dataclass(slots=True)
class _ConfigNamespace:
    """Mirror of ``mcp_agent.context.config`` exposing the bits DeepCode reads."""

    mcp: _MCPNamespace


@dataclass(slots=True)
class _ContextNamespace:
    """Mirror of ``mcp_agent.context``."""

    config: _ConfigNamespace


class DeepCodeRuntime:
    """Owns the loaded DeepCode config + provider cache for one process."""

    def __init__(
        self,
        config: DeepCodeConfig,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self.config = config
        self.credential_store = credential_store or CredentialStore()
        self.connection_resolver = ConnectionResolver(config, self.credential_store)
        self.model_catalog = ModelCatalogService()
        self.logger = logger
        self._provider_cache: dict[tuple[str, ...], LLMProvider] = {}
        # MCP servers materialised on construction so legacy callers can
        # mutate ``args`` in place (workflows.environment, plugin code, ...).
        self._mcp_servers = config.mcp_servers
        mcp_namespace = _MCPNamespace(servers=self._mcp_servers)
        config_namespace = _ConfigNamespace(mcp=mcp_namespace)
        self.context = _ContextNamespace(config=config_namespace)

    @classmethod
    def load(cls, config_path: str | None = None) -> "DeepCodeRuntime":
        """Read ``deepcode_config.json`` and build a fresh runtime."""
        return cls(load_config(config_path=config_path))

    def provider_for(
        self,
        *,
        provider_name: str | None = None,
        connection_id: str | None = None,
        phase: str = "default",
        model: str | None = None,
        execution_profile: ExecutionProfile | None = None,
    ) -> LLMProvider:
        """Return a cached :class:`LLMProvider` for the requested combination."""
        if execution_profile is not None or connection_id is not None:
            profile = execution_profile or self.resolve_execution_profile(
                connection_id=connection_id,
                model=model,
                phase=phase,
            )
            connection = self.connection_resolver.connection_for_profile(profile)
            credential_revision = hashlib.sha256(
                (connection.api_key or "").encode()
            ).hexdigest()[:16]
            cache_key = (
                profile.connection_id,
                profile.model_id,
                profile.config_revision,
                credential_revision,
                str(profile.max_tokens),
                repr(profile.temperature),
                profile.reasoning_effort or "",
            )
            cached = self._provider_cache.get(cache_key)
            if cached is not None:
                return cached
            provider = self.connection_resolver.build_provider(profile)
            self._provider_cache[cache_key] = provider
            return provider

        chosen_provider = (provider_name or self.config.llm_provider or "auto").lower()
        if chosen_provider == "google":
            chosen_provider = "gemini"
        cache_key = (chosen_provider, phase, model or "")
        cached = self._provider_cache.get(cache_key)
        if cached is not None:
            return cached
        provider = make_llm_provider(
            self.config,
            model=model,
            provider_name=None if chosen_provider == "auto" else chosen_provider,
            phase=phase,
        )
        self._provider_cache[cache_key] = provider
        return provider

    def resolve_execution_profile(
        self,
        *,
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        phase: str = "implementation",
    ) -> ExecutionProfile:
        selection = ExecutionSelection(
            connection_id=connection_id,
            model_id=model,
            reasoning_effort=reasoning_effort,
        )
        connection, resolved_model = self.connection_resolver.resolve_selection(
            selection,
            phase=phase,
        )
        cached = self.model_catalog.cached_model(connection, resolved_model)
        return self.connection_resolver.execution_profile(
            selection,
            phase=phase,
            model_limits=(
                (cached.context_window, cached.max_output_tokens)
                if cached is not None
                else None
            ),
            reasoning_capabilities=(cached.reasoning if cached is not None else None),
        )

    def get_provider_config(self, name: str | None = None) -> ProviderConfig | None:
        """Return the :class:`ProviderConfig` block for ``name`` (or the
        currently active provider when ``name`` is ``None``)."""
        if name:
            return getattr(self.config.providers, name.lower(), None)
        return self.config.get_provider()

    @property
    def mcp_servers(self) -> dict[str, MCPServerConfig]:
        """Live, mutable view of the MCP server map."""
        return self._mcp_servers


_runtime_override: ContextVar[DeepCodeRuntime | None] = ContextVar(
    "deepcode_runtime_override",
    default=None,
)


def get_runtime() -> DeepCodeRuntime:
    """Return the context-bound runtime or the lazy process-wide default."""
    override = _runtime_override.get()
    if override is not None:
        return override
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = DeepCodeRuntime.load()
    return _runtime


@contextmanager
def use_runtime(runtime: DeepCodeRuntime) -> Iterator[DeepCodeRuntime]:
    """Bind ``runtime`` to the current async/thread context and restore it."""
    token = _runtime_override.set(runtime)
    try:
        yield runtime
    finally:
        _runtime_override.reset(token)


def set_runtime(runtime: DeepCodeRuntime | None) -> None:
    """Override the process-wide runtime (useful for tests / reloads)."""
    global _runtime
    with _runtime_lock:
        _runtime = runtime
