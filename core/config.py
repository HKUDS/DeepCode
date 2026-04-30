"""DeepCode runtime configuration (single JSON file, nanobot-style).

This module is the single source of truth for DeepCode's runtime settings.
A user keeps everything in one ``deepcode_config.json`` next to the project
root (or any directory above the current working directory): provider keys,
phase-specific models, MCP servers, workspace, document segmentation, and
logger options all live in the same file.

The schema mirrors ``nanobot.config.schema`` (camelCase keys, Pydantic
``BaseModel`` per section) and is extended with DeepCode-specific blocks
(``workspace``, ``documentSegmentation``, ``logger``, ``llmLogger``).

Public API:

- :class:`DeepCodeConfig` – parsed configuration object
- :class:`AgentDefaults`, :class:`AgentPhase`, :class:`ProviderConfig`,
  :class:`ToolsConfig`, :class:`WorkspaceConfig`,
  :class:`DocumentSegmentationConfig`, :class:`LoggerConfig`,
  :class:`LLMLoggerConfig` – sub-models
- :func:`load_config` – read JSON and resolve ``${ENV_VAR}`` references
- :func:`make_llm_provider` – build the right
  :class:`core.providers.base.LLMProvider` for a workflow phase
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from core.agent_runtime.tools.mcp import MCPServerConfig
from core.providers.base import GenerationSettings, LLMProvider
from core.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_name,
)


_DEFAULT_CONFIG_FILENAME = "deepcode_config.json"
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


class AgentDefaults(_Base):
    """Default LLM generation settings shared by all phases."""

    provider: str = "auto"  # "auto" or registry name (e.g. "openai", "anthropic")
    model: str = "openai/gpt-4o-mini"
    max_tokens: int = 8192
    temperature: float = 0.1
    reasoning_effort: str | None = None
    # DeepCode-specific token policy fields used by retry logic.
    base_max_tokens: int | None = None
    retry_max_tokens: int | None = None
    max_tokens_policy: str | None = None
    # Runner ergonomics (mirror nanobot's AgentDefaults).
    max_tool_iterations: int = 200
    max_tool_result_chars: int = 16_000
    context_window_tokens: int = 65_536


class AgentPhase(_Base):
    """Per-phase overrides. Unset fields fall back to :class:`AgentDefaults`."""

    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


class AgentsConfig(_Base):
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    planning: AgentPhase = Field(default_factory=AgentPhase)
    implementation: AgentPhase = Field(default_factory=AgentPhase)


@dataclass(frozen=True, slots=True)
class ResolvedAgentSettings:
    """Phase + defaults merged into one immutable view."""

    provider: str
    model: str
    max_tokens: int
    temperature: float
    reasoning_effort: str | None
    base_max_tokens: int | None
    retry_max_tokens: int | None
    max_tokens_policy: str | None


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


class ProviderConfig(_Base):
    """LLM provider connection block.

    ``apiKey`` may be a literal key or a ``${ENV_VAR}`` reference resolved at
    load time.
    """

    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None


class ProvidersConfig(_Base):
    """Per-provider connection blocks. Add new providers by extending here
    and adding the matching :class:`~core.providers.registry.ProviderSpec`.
    """

    custom: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)


# ---------------------------------------------------------------------------
# tools / MCP
# ---------------------------------------------------------------------------


class MCPServerSchema(_Base):
    """JSON shape for one MCP server entry."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])
    tool_timeout: int = 300
    description: str | None = None


class ToolsConfig(_Base):
    default_search_server: str = "filesystem"
    mcp_servers: dict[str, MCPServerSchema] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# DeepCode-specific
# ---------------------------------------------------------------------------


class WorkspaceConfig(_Base):
    root: str = "./deepcode_lab"
    max_input_mb: int = 100


class DocumentSegmentationConfig(_Base):
    enabled: bool = True
    size_threshold_chars: int = 50000


class LoggerPathSettings(_Base):
    """Legacy block kept for backward compatibility.

    The new :class:`LoggerConfig` uses :class:`LoggerGlobalFile` /
    :class:`LoggerTaskFile` / :class:`LoggerLLMSink` instead. This block
    is no longer read by the runtime but is preserved so existing
    ``deepcode_config.json`` files keep validating.
    """

    path_pattern: str = "logs/deepcode-{unique_id}.jsonl"
    timestamp_format: str = "%Y%m%d_%H%M%S"
    unique_id: str = "timestamp"


class LoggerGlobalFile(_Base):
    """Global server-wide log sink (rotating)."""

    enabled: bool = True
    path_pattern: str = "logs/server-{date}.jsonl"
    rotation: str = "00:00"
    retention: str = "14 days"


class LoggerTaskFile(_Base):
    """Per-task JSONL sink under ``deepcode_lab/tasks/<id>/logs/``."""

    enabled: bool = True


class LoggerLLMSink(_Base):
    """LLM call recorder (writes to ``llm.jsonl`` per task)."""

    enabled: bool = True
    truncate_preview_chars: int = 2000


class LoggerConfig(_Base):
    """Unified logger configuration consumed by ``core.observability``.

    ``transports`` accepts the symbolic names ``console`` / ``global_file``
    / ``task_file`` (any subset). The legacy value ``"file"`` enables both
    file sinks.
    """

    level: str = "info"
    progress_display: bool = False
    transports: list[str] = Field(
        default_factory=lambda: ["console", "global_file", "task_file"]
    )
    global_file: LoggerGlobalFile = Field(default_factory=LoggerGlobalFile)
    task_file: LoggerTaskFile = Field(default_factory=LoggerTaskFile)
    llm: LoggerLLMSink = Field(default_factory=LoggerLLMSink)
    path_settings: LoggerPathSettings = Field(default_factory=LoggerPathSettings)


class LLMLoggerConfig(_Base):
    enabled: bool = True
    output_format: str = "json"
    log_level: str = "basic"
    log_directory: str = "logs/llm_responses"
    filename_pattern: str = "llm_responses_{timestamp}.jsonl"
    include_models: list[str] = Field(default_factory=list)
    min_response_length: int = 50


# ---------------------------------------------------------------------------
# root
# ---------------------------------------------------------------------------


class DeepCodeConfig(BaseSettings):
    """Root configuration loaded from ``deepcode_config.json``."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    document_segmentation: DocumentSegmentationConfig = Field(
        default_factory=DocumentSegmentationConfig,
        validation_alias=AliasChoices("documentSegmentation", "document_segmentation"),
    )
    logger: LoggerConfig = Field(default_factory=LoggerConfig)
    llm_logger: LLMLoggerConfig = Field(
        default_factory=LLMLoggerConfig,
        validation_alias=AliasChoices("llmLogger", "llm_logger"),
    )

    model_config = ConfigDict(
        env_prefix="DEEPCODE_",
        env_nested_delimiter="__",
        populate_by_name=True,
        extra="ignore",
    )

    # ---- legacy/compat field accessors ----

    @property
    def llm_provider(self) -> str:
        """Forced provider name (or ``"auto"``). Mirrors the old YAML field."""
        return self.agents.defaults.provider or "auto"

    @property
    def mcp_servers(self) -> dict[str, MCPServerConfig]:
        """Materialise MCP servers as the dataclass expected by the runtime.

        ``core.agent_runtime.tools.mcp`` consumes :class:`MCPServerConfig` (a
        slim dataclass), not the Pydantic schema, so we adapt here and keep
        ``self.tools.mcp_servers`` as the single edit surface.
        """
        return {
            name: MCPServerConfig(
                name=name,
                type=server.type,
                command=server.command or None,
                args=list(server.args),
                env=dict(server.env) if server.env else None,
                url=server.url or None,
                headers=dict(server.headers) if server.headers else None,
                enabled_tools=list(server.enabled_tools) or ["*"],
                tool_timeout=server.tool_timeout,
                description=server.description,
            )
            for name, server in self.tools.mcp_servers.items()
        }

    # ---- phase resolution ----

    def resolve_phase(self, phase: str = "default") -> ResolvedAgentSettings:
        """Merge ``agents.defaults`` with the phase override (if any)."""
        defaults = self.agents.defaults
        override: AgentPhase | None
        if phase == "planning":
            override = self.agents.planning
        elif phase == "implementation":
            override = self.agents.implementation
        else:
            override = None

        def _pick(name: str) -> Any:
            if override is not None:
                value = getattr(override, name)
                if value is not None:
                    return value
            return getattr(defaults, name)

        return ResolvedAgentSettings(
            provider=_pick("provider"),
            model=_pick("model"),
            max_tokens=_pick("max_tokens"),
            temperature=_pick("temperature"),
            reasoning_effort=_pick("reasoning_effort"),
            base_max_tokens=defaults.base_max_tokens,
            retry_max_tokens=defaults.retry_max_tokens,
            max_tokens_policy=defaults.max_tokens_policy,
        )

    def model_for_phase(self, phase: str = "default") -> str:
        """Return the resolved model for a phase, raising on misconfiguration."""
        chosen = (self.resolve_phase(phase).model or "").strip()
        if not chosen:
            raise ValueError(f"No model configured for phase '{phase}'")
        return chosen

    # ---- provider matching (mirrors nanobot.config.schema._match_provider) ----

    def _match_provider(
        self, model: str | None = None, *, forced_provider: str | None = None
    ) -> tuple[ProviderConfig | None, str | None]:
        """Return ``(ProviderConfig, registry_name)`` for ``model``.

        Resolution priority:

        1. Explicit ``forced_provider`` (or ``agents.defaults.provider`` if
           set to anything other than ``"auto"``).
        2. Model prefix exact match (e.g. ``openai/gpt-5.4``).
        3. Provider keyword match against the model name.
        4. Local providers (vLLM/Ollama) configured with an ``apiBase``.
        5. First non-OAuth provider that has an ``apiKey`` set.
        """
        forced = (forced_provider or self.agents.defaults.provider or "auto").lower()
        if forced != "auto":
            spec = find_by_name(forced)
            if spec is None:
                return None, None
            p = getattr(self.providers, spec.name, None)
            return (p, spec.name) if p is not None else (None, None)

        model_lower = (model or self.agents.defaults.model or "").lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p is not None and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or spec.is_direct or p.api_key:
                    return p, spec.name

        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p is not None and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or spec.is_local or spec.is_direct or p.api_key:
                    return p, spec.name

        local_fallback: tuple[ProviderConfig, str] | None = None
        for spec in PROVIDERS:
            if not spec.is_local:
                continue
            p = getattr(self.providers, spec.name, None)
            if not (p and p.api_base):
                continue
            if (
                spec.detect_by_base_keyword
                and spec.detect_by_base_keyword in p.api_base
            ):
                return p, spec.name
            if local_fallback is None:
                local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p is not None and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        return self._match_provider(model)[0]

    def get_provider_name(self, model: str | None = None) -> str | None:
        return self._match_provider(model)[1]

    def get_api_base(self, model: str | None = None) -> str | None:
        p, name = self._match_provider(model)
        if p is not None and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _resolve_workspace_path(start: Path | None = None) -> Path:
    """Find the project root by looking for ``deepcode_config.json`` upwards."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _DEFAULT_CONFIG_FILENAME).exists():
            return candidate
    return here


def default_config_path() -> Path:
    """Return the resolved default ``deepcode_config.json`` location."""
    return _resolve_workspace_path() / _DEFAULT_CONFIG_FILENAME


def _resolve_env_refs(value: Any, *, path: str = "") -> Any:
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            env_value = os.environ.get(name)
            if env_value is None:
                where = f" at {path}" if path else ""
                raise ValueError(
                    f"Environment variable '{name}' referenced in deepcode_config.json{where} is not set"
                )
            return env_value

        return _ENV_REF_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {
            k: _resolve_env_refs(v, path=f"{path}.{k}" if path else k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_env_refs(item, path=f"{path}[{i}]") for i, item in enumerate(value)
        ]
    return value


def load_config(config_path: str | Path | None = None) -> DeepCodeConfig:
    """Load and parse ``deepcode_config.json``.

    When ``config_path`` is ``None`` the loader walks up from the current
    working directory looking for ``deepcode_config.json``. When the file is
    absent, defaults are returned (no provider keys, no MCP servers) so the
    process can still boot for diagnostic commands.
    """
    if config_path is None:
        resolved = _resolve_workspace_path() / _DEFAULT_CONFIG_FILENAME
    else:
        resolved = Path(config_path).expanduser().resolve()

    raw: dict[str, Any] = {}
    if resolved.exists():
        try:
            with resolved.open("r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {resolved}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(
                f"Top-level of {resolved} must be a JSON object (got {type(raw).__name__})"
            )
    else:
        logger.debug("deepcode_config.json not found at {}; using defaults", resolved)

    raw = _resolve_env_refs(raw)
    return DeepCodeConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def _resolve_spec_for_phase(
    config: DeepCodeConfig,
    phase: str,
    *,
    provider_override: str | None,
    model_override: str | None,
) -> tuple[ProviderConfig | None, ProviderSpec | None, str, ResolvedAgentSettings]:
    """Pick the provider config + registry spec + chosen model for a phase."""
    settings = config.resolve_phase(phase)
    chosen_model = (model_override or settings.model or "").strip()
    if not chosen_model:
        raise ValueError(f"No model configured for phase '{phase}'")

    forced = (provider_override or settings.provider or "auto").lower()
    if forced != "auto":
        spec = find_by_name(forced)
        if spec is None:
            raise ValueError(
                f"Provider '{forced}' (phase '{phase}') is not registered in core.providers.registry"
            )
        p = getattr(config.providers, spec.name, None)
        return p, spec, chosen_model, settings

    matched_cfg, matched_name = config._match_provider(chosen_model)
    spec = find_by_name(matched_name) if matched_name else None
    return matched_cfg, spec, chosen_model, settings


def make_llm_provider(
    config: DeepCodeConfig,
    *,
    phase: str = "default",
    model: str | None = None,
    provider_name: str | None = None,
) -> LLMProvider:
    """Instantiate the right :class:`LLMProvider` for the requested phase.

    Provider resolution mirrors nanobot's ``_make_provider``: the matched
    :class:`~core.providers.registry.ProviderSpec` decides which backend
    (``openai_compat``, ``anthropic``, ...) is instantiated. ``GenerationSettings``
    are derived from the resolved phase settings.
    """
    provider_cfg, spec, chosen_model, settings = _resolve_spec_for_phase(
        config, phase, provider_override=provider_name, model_override=model
    )
    if spec is None:
        raise ValueError(
            f"Could not match a provider for model '{chosen_model}' (phase '{phase}'). "
            "Set agents.defaults.provider or fill in the matching providers.<name>.apiKey."
        )

    backend = spec.backend
    api_key = provider_cfg.api_key if provider_cfg else None
    api_base = provider_cfg.api_base if provider_cfg else None
    extra_headers = provider_cfg.extra_headers if provider_cfg else None

    needs_key = not (spec.is_oauth or spec.is_local or spec.is_direct)
    if needs_key and not api_key:
        raise ValueError(
            f"Provider '{spec.name}' (phase '{phase}') requires providers.{spec.name}.apiKey "
            "in deepcode_config.json"
        )

    effective_base = api_base or spec.default_api_base or None

    if backend == "anthropic":
        from core.providers.anthropic import AnthropicProvider

        provider: LLMProvider = AnthropicProvider(
            api_key=api_key,
            api_base=effective_base,
            default_model=chosen_model,
            extra_headers=extra_headers,
        )
    elif backend == "openai_compat":
        from core.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=effective_base,
            default_model=chosen_model,
            extra_headers=extra_headers,
            spec=spec,
        )
    else:
        raise ValueError(
            f"Unsupported provider backend '{backend}' for spec '{spec.name}'"
        )

    provider.generation = GenerationSettings(
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        reasoning_effort=settings.reasoning_effort,
    )
    return provider


__all__ = [
    "AgentDefaults",
    "AgentPhase",
    "AgentsConfig",
    "DeepCodeConfig",
    "DocumentSegmentationConfig",
    "LLMLoggerConfig",
    "LoggerConfig",
    "LoggerGlobalFile",
    "LoggerLLMSink",
    "LoggerPathSettings",
    "LoggerTaskFile",
    "MCPServerSchema",
    "ProviderConfig",
    "ProvidersConfig",
    "ResolvedAgentSettings",
    "ToolsConfig",
    "WorkspaceConfig",
    "default_config_path",
    "load_config",
    "make_llm_provider",
]
