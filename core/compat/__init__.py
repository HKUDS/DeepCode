"""DeepCode compatibility layer for legacy ``mcp_agent`` imports.

The legacy code base wired itself to ``mcp_agent`` types such as
:class:`MCPApp`, :class:`Agent`, :class:`RequestParams`, ``AugmentedLLM``,
and :class:`ParallelLLM`. To avoid touching every callsite we re-export
DeepCode-native replacements here that share the same surface, but are
implemented on top of the new ``core`` runtime (provider/tool/runner stack
ported from ``nanobot``).

Public surface (mirrors ``mcp_agent``):

* :class:`MCPApp` - holds runtime state, exposes ``run()``/``stop()``.
* :class:`Agent` - tool-capable agent, supports ``__aenter__``,
  ``__aexit__``, ``call_tool``, ``list_tools`` and ``attach_llm``.
* :class:`AugmentedLLM` - returned from :meth:`Agent.attach_llm`. Provides
  ``generate_str(message, request_params=...)`` like the legacy class.
* :class:`AnthropicAugmentedLLM`, :class:`OpenAIAugmentedLLM`,
  :class:`GoogleAugmentedLLM` - thin marker subclasses so callers can keep
  passing a class to ``attach_llm``.
* :class:`RequestParams` - dataclass with ``max_tokens``, ``temperature``,
  ``model``, ``maxTokens``, ``use_history``, ``max_iterations`` (matches
  the legacy fields used in DeepCode).
* :class:`ParallelLLM` - simple parallel fan-out / fan-in helper.
"""

from core.compat.agent import (
    Agent,
    AnthropicAugmentedLLM,
    AugmentedLLM,
    GoogleAugmentedLLM,
    OpenAIAugmentedLLM,
)
from core.compat.mcp_app import MCPApp
from core.compat.parallel import ParallelLLM
from core.compat.request_params import RequestParams
from core.compat.runtime import (
    DeepCodeRuntime,
    get_runtime,
    set_runtime,
)

__all__ = [
    "Agent",
    "AnthropicAugmentedLLM",
    "AugmentedLLM",
    "DeepCodeRuntime",
    "GoogleAugmentedLLM",
    "MCPApp",
    "OpenAIAugmentedLLM",
    "ParallelLLM",
    "RequestParams",
    "get_runtime",
    "set_runtime",
]
