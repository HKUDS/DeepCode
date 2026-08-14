"""Drop-in replacement for ``mcp_agent.workflows.llm.augmented_llm.RequestParams``.

The legacy class accepted a kitchen sink of fields; only the ones DeepCode
actually reads are modelled as first-class attributes here. ``maxTokens`` is
preserved as an alias of ``max_tokens`` because both spellings appear in the
legacy code base.

Unknown keyword arguments are accepted defensively (collected into
``metadata``) and a single ``logger.warning`` is emitted per unknown field
name to make migration drift from old ``mcp_agent`` callsites visible
without crashing the pipeline. This is the lesson learned from the
``tool_filter`` regression: silently breaking on a forgotten kwarg blows up
the whole multi-agent pipeline mid-run.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


_KNOWN_FIELDS = frozenset(
    {
        "max_tokens",
        "maxTokens",
        "temperature",
        "reasoning_effort",
        "model",
        "use_history",
        "max_iterations",
        "parallel_tool_calls",
        "tool_filter",
        "max_tool_result_chars",
        "context_window_tokens",
        "context_block_limit",
        "provider_retry_mode",
        "retry_wait_callback",
        "checkpoint_callback",
        "llm_timeout_s",
        "enforce_default_max_iterations",
        "metadata",
    }
)

# Names we have already warned about in this process. Keyed by the field
# name only (not by callsite) so the log stays signal-not-noise.
_WARNED_UNKNOWN_FIELDS: set[str] = set()


class RequestParams:
    """LLM request parameters carried alongside ``generate_str`` calls.

    ``tool_filter`` mirrors the legacy ``mcp_agent`` field: a mapping of
    ``{mcp_server_name: {allowed_tool_names}}``. ``None`` (the default)
    means **every** registered tool is exposed to the model. An empty dict
    is treated the same as ``None`` (no filtering). When the dict is
    non-empty, only the tools whose underlying MCP server appears in the
    map are kept, and within each listed server only the listed tool names
    are kept (an empty set for a server expands to "all tools from that
    server"). Non-MCP tools (those whose name does not start with
    ``mcp_``) are always preserved.
    """

    __slots__ = (
        "max_tokens",
        "maxTokens",
        "temperature",
        "reasoning_effort",
        "model",
        "use_history",
        "max_iterations",
        "parallel_tool_calls",
        "tool_filter",
        "max_tool_result_chars",
        "context_window_tokens",
        "context_block_limit",
        "provider_retry_mode",
        "retry_wait_callback",
        "checkpoint_callback",
        "llm_timeout_s",
        "enforce_default_max_iterations",
        "metadata",
    )

    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        maxTokens: int | None = None,  # noqa: N803 - legacy spelling
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        model: str | None = None,
        use_history: bool = True,
        max_iterations: int = 1,
        parallel_tool_calls: bool = False,
        tool_filter: dict[str, set[str]] | None = None,
        max_tool_result_chars: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        provider_retry_mode: str = "standard",
        retry_wait_callback: Any | None = None,
        checkpoint_callback: Any | None = None,
        llm_timeout_s: float | None = None,
        enforce_default_max_iterations: bool = True,
        metadata: dict[str, Any] | None = None,
        **unknown: Any,
    ) -> None:
        self.max_tokens = max_tokens
        self.maxTokens = maxTokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.model = model
        self.use_history = use_history
        self.max_iterations = max_iterations
        self.parallel_tool_calls = parallel_tool_calls
        self.tool_filter = tool_filter
        self.max_tool_result_chars = max_tool_result_chars
        self.context_window_tokens = context_window_tokens
        self.context_block_limit = context_block_limit
        self.provider_retry_mode = provider_retry_mode
        self.retry_wait_callback = retry_wait_callback
        self.checkpoint_callback = checkpoint_callback
        self.llm_timeout_s = llm_timeout_s
        self.enforce_default_max_iterations = enforce_default_max_iterations
        self.metadata = dict(metadata) if metadata else None

        if unknown:
            new_names = [name for name in unknown if name not in _WARNED_UNKNOWN_FIELDS]
            if new_names:
                _WARNED_UNKNOWN_FIELDS.update(new_names)
                logger.warning(
                    "RequestParams: ignoring unknown kwargs {}. These look like "
                    "leftover legacy mcp_agent fields. Add them to "
                    "core.compat.request_params._KNOWN_FIELDS (and wire them "
                    "through AugmentedLLM) if you actually need their behaviour.",
                    new_names,
                )
            self.metadata = {**(self.metadata or {}), **unknown}

    def resolved_max_tokens(self) -> int | None:
        """Pick the first non-``None`` of ``max_tokens`` / ``maxTokens``."""
        if self.max_tokens is not None:
            return self.max_tokens
        return self.maxTokens

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        body = ", ".join(f"{k}={getattr(self, k)!r}" for k in self.__slots__)
        return f"RequestParams({body})"
