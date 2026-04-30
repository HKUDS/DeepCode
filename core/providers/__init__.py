"""LLM provider abstractions ported from nanobot.

- :class:`LLMProvider` lives in :mod:`core.providers.base` and defines the
  unified ``chat`` / ``chat_stream`` / ``chat_with_retry`` surface used by
  :class:`core.agent_runtime.runner.AgentRunner`.
- :class:`OpenAICompatProvider` (``openai`` SDK) and
  :class:`AnthropicProvider` (``anthropic`` SDK) are concrete backends.
- :data:`PROVIDERS`, :func:`find_by_name`, and :func:`find_by_model` in
  :mod:`core.providers.registry` route models -> providers.
"""

from core.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
)
from core.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_model,
    find_by_name,
)

__all__ = [
    "PROVIDERS",
    "GenerationSettings",
    "LLMProvider",
    "LLMResponse",
    "ProviderSpec",
    "ToolCallRequest",
    "find_by_model",
    "find_by_name",
]
