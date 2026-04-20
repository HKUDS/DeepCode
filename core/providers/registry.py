"""Provider registry — single source of truth for LLM provider metadata.

Pruned port of ``nanobot.providers.registry``. Includes only the providers
DeepCode actively supports today; add new entries by appending a
:class:`ProviderSpec` to :data:`PROVIDERS`.

Adding a new provider:
  1. Add a ``ProviderSpec`` to ``PROVIDERS`` below (order = match priority).
  2. If you need a new backend, instantiate it in
     :func:`core.config.make_llm_provider` based on ``spec.backend``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SNAKE_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(name: str) -> str:
    return _SNAKE_PATTERN.sub("_", name).lower()


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata.

    Placeholders in ``env_extras`` values:

    - ``{api_key}`` — the user's API key
    - ``{api_base}`` — ``api_base`` from config, or this spec's
      ``default_api_base``
    """

    name: str
    keywords: tuple[str, ...]
    env_key: str
    display_name: str = ""
    backend: str = "openai_compat"
    env_extras: tuple[tuple[str, str], ...] = ()
    is_gateway: bool = False
    is_local: bool = False
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    default_api_base: str = ""
    strip_model_prefix: bool = False
    supports_max_completion_tokens: bool = False
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = field(default_factory=tuple)
    is_oauth: bool = False
    is_direct: bool = False
    supports_prompt_caching: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
    ),
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
    ),
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        default_api_base="https://api.deepseek.com",
    ),
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        backend="openai_compat",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        backend="openai_compat",
        env_extras=(("ZHIPUAI_API_KEY", "{api_key}"),),
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
    ),
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        backend="openai_compat",
        is_local=True,
    ),
    ProviderSpec(
        name="ollama",
        keywords=("ollama", "nemotron"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
)


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. ``"deepseek"``."""
    normalized = _to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None


def find_by_model(
    model: str | None,
    *,
    available_provider_names: set[str] | None = None,
) -> ProviderSpec | None:
    """Match a provider spec by model name keywords.

    If ``available_provider_names`` is supplied, providers absent from that
    set are skipped; otherwise every spec is considered. Returns ``None`` when
    nothing matches.
    """
    if not model:
        return None

    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    def _kw_matches(kw: str) -> bool:
        kw = kw.lower()
        return kw in model_lower or kw.replace("-", "_") in model_normalized

    def _eligible(spec: ProviderSpec) -> bool:
        if available_provider_names is None:
            return True
        return spec.name in available_provider_names

    for spec in PROVIDERS:
        if _eligible(spec) and model_prefix and normalized_prefix == spec.name:
            return spec

    for spec in PROVIDERS:
        if _eligible(spec) and any(_kw_matches(kw) for kw in spec.keywords):
            return spec

    return None
