"""DeepCode runtime configuration: yaml -> provider/MCP wiring.

Replaces the ``mcp_agent.config`` parsing layer that ``mcp_agent`` used to do.
Reads ``mcp_agent.config.yaml`` + ``mcp_agent.secrets.yaml`` (kept for
backwards compatibility with existing user setups), exposes a typed
``DeepCodeConfig`` view, and instantiates the right
:class:`core.providers.base.LLMProvider` for the configured model.

Public API:

- :func:`load_config` - read and merge config + secrets
- :func:`make_llm_provider` - instantiate the matched provider
- :class:`DeepCodeConfig` - dataclass view of the merged config
- :class:`LLMProviderConfig`, :class:`MCPServerSpec` - sub-configs
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from core.agent_runtime.tools.mcp import MCPServerConfig
from core.providers.base import GenerationSettings, LLMProvider
from core.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_model,
    find_by_name,
)

_DEFAULT_CONFIG_FILENAME = "mcp_agent.config.yaml"
_DEFAULT_SECRETS_FILENAME = "mcp_agent.secrets.yaml"


@dataclass(slots=True)
class LLMProviderConfig:
    """Per-provider config block (api_key + base_url + per-phase models)."""

    name: str
    api_key: str | None = None
    api_base: str | None = None
    default_model: str | None = None
    planning_model: str | None = None
    implementation_model: str | None = None
    reasoning_effort: str | None = None
    base_max_tokens: int | None = None
    retry_max_tokens: int | None = None
    max_tokens_policy: str | None = None
    extra_headers: dict[str, str] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def model_for(self, phase: str) -> str | None:
        """Pick a model for ``"default" | "planning" | "implementation"``."""
        if phase == "planning" and self.planning_model:
            return self.planning_model
        if phase == "implementation" and self.implementation_model:
            return self.implementation_model
        return self.default_model


@dataclass(slots=True)
class DeepCodeConfig:
    """Merged DeepCode configuration."""

    llm_provider: str
    providers: dict[str, LLMProviderConfig]
    mcp_servers: dict[str, MCPServerConfig]
    raw: dict[str, Any]

    def get_provider_config(self, name: str | None = None) -> LLMProviderConfig:
        """Return the provider block for *name* (default: ``llm_provider``)."""
        chosen = name or self.llm_provider
        if chosen not in self.providers:
            raise KeyError(
                f"LLM provider '{chosen}' not configured. "
                f"Available: {sorted(self.providers)}"
            )
        return self.providers[chosen]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level of {path}, got {type(data).__name__}")
    return data


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_provider_config(
    name: str,
    block: dict[str, Any],
    secrets_block: dict[str, Any],
) -> LLMProviderConfig:
    api_key = (
        secrets_block.get("api_key")
        or block.get("api_key")
        or os.environ.get(f"{name.upper()}_API_KEY")
    )
    api_base = (
        secrets_block.get("base_url")
        or secrets_block.get("api_base")
        or block.get("base_url")
        or block.get("api_base")
    )

    extra_headers_block = block.get("extra_headers") or secrets_block.get("extra_headers")
    extra_headers: dict[str, str] | None = None
    if isinstance(extra_headers_block, dict):
        extra_headers = {str(k): str(v) for k, v in extra_headers_block.items()}

    return LLMProviderConfig(
        name=name,
        api_key=api_key or None,
        api_base=api_base or None,
        default_model=block.get("default_model"),
        planning_model=block.get("planning_model"),
        implementation_model=block.get("implementation_model"),
        reasoning_effort=block.get("reasoning_effort"),
        base_max_tokens=_coerce_int(block.get("base_max_tokens")),
        retry_max_tokens=_coerce_int(block.get("retry_max_tokens")),
        max_tokens_policy=block.get("max_tokens_policy"),
        extra_headers=extra_headers,
        raw=block,
    )


def _build_mcp_servers(servers_block: dict[str, Any]) -> dict[str, MCPServerConfig]:
    servers: dict[str, MCPServerConfig] = {}
    for name, raw in (servers_block or {}).items():
        if not isinstance(raw, dict):
            logger.warning("Ignoring malformed MCP server block '{}'", name)
            continue

        env: dict[str, str] | None = None
        if isinstance(raw.get("env"), dict):
            env = {str(k): str(v) for k, v in raw["env"].items()}

        enabled_tools_raw = raw.get("enabledTools") or raw.get("enabled_tools")
        if isinstance(enabled_tools_raw, list) and enabled_tools_raw:
            enabled_tools = [str(item) for item in enabled_tools_raw]
        else:
            enabled_tools = ["*"]

        headers_raw = raw.get("headers")
        headers: dict[str, str] | None = None
        if isinstance(headers_raw, dict):
            headers = {str(k): str(v) for k, v in headers_raw.items()}

        servers[name] = MCPServerConfig(
            name=name,
            type=raw.get("type"),
            command=raw.get("command"),
            args=[str(item) for item in (raw.get("args") or [])],
            env=env,
            url=raw.get("url"),
            headers=headers,
            enabled_tools=enabled_tools,
            tool_timeout=int(raw.get("tool_timeout", 300)),
            description=raw.get("description"),
        )
    return servers


def _resolve_workspace_path(start: Path | None = None) -> Path:
    """Find the project root by looking for the config file, falling back to cwd."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _DEFAULT_CONFIG_FILENAME).exists():
            return candidate
    return here


def load_config(
    config_path: str | Path | None = None,
    secrets_path: str | Path | None = None,
) -> DeepCodeConfig:
    """Load and merge the YAML config + secrets files.

    When paths are omitted DeepCode looks for ``mcp_agent.config.yaml`` and
    ``mcp_agent.secrets.yaml`` next to it, walking up from ``cwd``.
    """
    if config_path is None:
        root = _resolve_workspace_path()
        config_path = root / _DEFAULT_CONFIG_FILENAME
    config_path = Path(config_path).resolve()

    if secrets_path is None:
        secrets_path = config_path.parent / _DEFAULT_SECRETS_FILENAME
    secrets_path = Path(secrets_path).resolve()

    raw_config = _load_yaml(config_path)
    raw_secrets = _load_yaml(secrets_path)

    llm_provider = (raw_config.get("llm_provider") or "openai").strip().lower()

    provider_names = {spec.name for spec in PROVIDERS}
    config_provider_keys = {
        key for key, value in raw_config.items()
        if isinstance(value, dict) and (key in provider_names or key in raw_secrets)
    }
    secret_provider_keys = {
        key for key, value in raw_secrets.items() if isinstance(value, dict)
    }

    google_alias = "gemini" if "google" in (config_provider_keys | secret_provider_keys) else None

    providers: dict[str, LLMProviderConfig] = {}
    seen: set[str] = set()
    for key in config_provider_keys | secret_provider_keys:
        block = raw_config.get(key) if isinstance(raw_config.get(key), dict) else {}
        secrets_block = raw_secrets.get(key) if isinstance(raw_secrets.get(key), dict) else {}
        normalized_name = "gemini" if (key == "google" and google_alias) else key
        if normalized_name in seen:
            continue
        seen.add(normalized_name)
        providers[normalized_name] = _build_provider_config(
            normalized_name, block, secrets_block
        )

    if llm_provider == "google":
        llm_provider = "gemini"

    if llm_provider not in providers:
        logger.warning(
            "Configured llm_provider='{}' not found; available providers: {}",
            llm_provider,
            sorted(providers),
        )

    mcp_block = (raw_config.get("mcp") or {}).get("servers") or {}
    mcp_servers = _build_mcp_servers(mcp_block)

    return DeepCodeConfig(
        llm_provider=llm_provider,
        providers=providers,
        mcp_servers=mcp_servers,
        raw=raw_config,
    )


def _resolve_provider_spec(
    config: DeepCodeConfig, provider_name: str | None, model: str | None
) -> tuple[LLMProviderConfig, ProviderSpec]:
    """Pick the provider config + registry spec for ``model``.

    Order:

    1. Honor ``provider_name`` if explicitly supplied.
    2. Honor ``config.llm_provider`` if its block exists.
    3. Match by model keywords, restricted to providers actually configured.
    """
    candidate_name: str | None = None
    if provider_name and provider_name in config.providers:
        candidate_name = provider_name
    elif config.llm_provider in config.providers:
        candidate_name = config.llm_provider

    if candidate_name is not None:
        spec = find_by_name(candidate_name)
        if spec is None:
            raise KeyError(
                f"Provider '{candidate_name}' is not registered in core.providers.registry"
            )
        return config.providers[candidate_name], spec

    matched = find_by_model(model, available_provider_names=set(config.providers))
    if matched is None:
        raise RuntimeError(
            f"Could not match a provider for model '{model}'. "
            f"Configured providers: {sorted(config.providers)}"
        )
    return config.providers[matched.name], matched


def make_llm_provider(
    config: DeepCodeConfig,
    *,
    model: str | None = None,
    provider_name: str | None = None,
    phase: str = "default",
) -> LLMProvider:
    """Instantiate the right :class:`LLMProvider` for the requested model.

    Looks the model up in :data:`PROVIDERS` (or honors ``provider_name``),
    then constructs either :class:`OpenAICompatProvider` or
    :class:`AnthropicProvider` based on ``spec.backend``. Generation defaults
    are pulled from the matched provider config block.
    """
    provider_cfg, spec = _resolve_provider_spec(config, provider_name, model)

    chosen_model = model or provider_cfg.model_for(phase) or provider_cfg.default_model
    if not chosen_model:
        raise ValueError(
            f"No model configured for provider '{provider_cfg.name}' (phase '{phase}')"
        )

    if spec.backend == "anthropic":
        from core.providers.anthropic import AnthropicProvider

        provider: LLMProvider = AnthropicProvider(
            api_key=provider_cfg.api_key,
            api_base=provider_cfg.api_base,
            default_model=chosen_model,
            extra_headers=provider_cfg.extra_headers,
        )
    elif spec.backend == "openai_compat":
        from core.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=provider_cfg.api_key,
            api_base=provider_cfg.api_base,
            default_model=chosen_model,
            extra_headers=provider_cfg.extra_headers,
            spec=spec,
        )
    else:
        raise ValueError(
            f"Unsupported provider backend '{spec.backend}' for provider '{spec.name}'"
        )

    provider.generation = GenerationSettings(
        temperature=0.7,
        max_tokens=provider_cfg.base_max_tokens or 4096,
        reasoning_effort=provider_cfg.reasoning_effort,
    )
    return provider
