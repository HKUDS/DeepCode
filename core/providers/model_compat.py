"""Declarative per-request wire behavior for OpenAI-compatible models.

Model-level quirks used to be scattered through
``OpenAICompatProvider._build_kwargs`` as ``model_name.lower()`` substring
checks (temperature support, reasoning-model detection, Kimi "thinking"
injection, effort spelling). That is the ``model_id.includes(...)``
anti-pattern (DEEPCODE_V2_MASTER_PLAN.md §8 #15): it silently rots as
model names change and hides capability decisions in control flow.

This module consolidates those decisions into one place. Given a
``(model, provider spec, request params)`` it resolves a single
:class:`ModelCompat` value describing exactly how the request should be
shaped. The provider then *assembles* the request from that value instead
of re-deriving quirks inline — mechanism, testable in isolation, one
source of truth.

Provider-*level* flags already live declaratively on
:class:`~core.providers.registry.ProviderSpec` (``strip_model_prefix``,
``supports_max_completion_tokens``, ``thinking_style``, ``model_overrides``);
this layer sits on top and folds in the per-*model* behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Model-name tokens marking a "reasoning" model that rejects a temperature
# parameter once reasoning is active (OpenAI gpt-5 family + o-series).
_REASONING_MODEL_TOKENS: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")

# Kimi (Moonshot) thinking-capable models: they take a top-level
# ``thinking: {type: enabled|disabled}`` in extra_body and want an empty
# ``reasoning_content`` echoed back on prior assistant turns. Kept in sync
# with the native Moonshot catalog.
_KIMI_THINKING_MODELS: frozenset[str] = frozenset(
    {
        "kimi-k2.5",
        "kimi-k2.6",
        "k2.6-code-preview",
    }
)

# thinking_style (a provider-level ProviderSpec flag) → builder of the
# extra_body fragment for a given enabled/disabled state.
_THINKING_STYLE_BUILDERS = {
    "thinking_type": lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
    "enable_thinking": lambda on: {"enable_thinking": on},
    "reasoning_split": lambda on: {"reasoning_split": on},
}


def _bare_model_name(model_name: str) -> str:
    """Strip a provider prefix (``moonshotai/kimi-k2.5`` → ``kimi-k2.5``)."""
    return model_name.rsplit("/", 1)[-1] if "/" in model_name else model_name


def is_reasoning_model(model_name: str) -> bool:
    name = model_name.lower()
    return any(token in name for token in _REASONING_MODEL_TOKENS)


def is_kimi_thinking_model(model_name: str) -> bool:
    name = model_name.lower()
    return (
        name in _KIMI_THINKING_MODELS or _bare_model_name(name) in _KIMI_THINKING_MODELS
    )


def normalize_effort(reasoning_effort: str | None) -> str | None:
    """Semantic effort: lowercased, with ``minimum`` folded to ``minimal``."""
    if not isinstance(reasoning_effort, str):
        return None
    effort = reasoning_effort.lower()
    return "minimal" if effort == "minimum" else effort


@dataclass(frozen=True)
class ModelCompat:
    """The resolved wire behavior for one request.

    Everything the assembler needs, already decided:

    * ``model_name`` — after any provider-prefix strip.
    * ``include_temperature`` — whether to send ``temperature``.
    * ``token_limit_field`` — ``"max_tokens"`` or ``"max_completion_tokens"``.
    * ``reasoning_effort_wire`` — the effort string to send (provider
      spelling), or ``None`` to omit.
    * ``thinking_extra_body`` — a fragment to merge into ``extra_body``, or
      ``None``.
    * ``inject_empty_reasoning_content`` — echo an empty ``reasoning_content``
      on prior assistant messages (required by thinking models).
    * ``model_overrides`` — provider-spec pattern overrides to apply.
    """

    model_name: str
    include_temperature: bool
    token_limit_field: str
    reasoning_effort_wire: str | None
    thinking_extra_body: dict[str, Any] | None
    inject_empty_reasoning_content: bool
    model_overrides: dict[str, Any] = field(default_factory=dict)


def resolve_model_compat(
    *,
    model_name: str,
    spec: Any | None,
    reasoning_effort: str | None,
) -> ModelCompat:
    """Resolve the per-request wire behavior for ``model_name``.

    Pure function of the model name, its :class:`ProviderSpec` and the
    requested reasoning effort. No IO, no globals — trivially testable.
    """
    resolved_name = model_name
    if spec is not None and getattr(spec, "strip_model_prefix", False):
        resolved_name = resolved_name.split("/")[-1]

    semantic_effort = normalize_effort(reasoning_effort)
    reasoning_active = (
        reasoning_effort is not None and reasoning_effort.lower() != "none"
    )

    # Temperature: reasoning models reject it while reasoning is active.
    include_temperature = not (reasoning_active and is_reasoning_model(resolved_name))

    # Token-limit field is a provider-level capability.
    token_limit_field = (
        "max_completion_tokens"
        if spec is not None and getattr(spec, "supports_max_completion_tokens", False)
        else "max_tokens"
    )

    # Reasoning effort wire spelling (DashScope wants "minimum" for "minimal").
    reasoning_effort_wire = reasoning_effort
    if (
        spec is not None
        and getattr(spec, "name", None) == "dashscope"
        and semantic_effort == "minimal"
    ):
        reasoning_effort_wire = "minimum"

    thinking_enabled = semantic_effort != "minimal"

    # Thinking extra_body: provider-level style OR model-level Kimi injection.
    thinking_extra_body: dict[str, Any] | None = None
    style = getattr(spec, "thinking_style", "") if spec is not None else ""
    if style and reasoning_effort is not None:
        builder = _THINKING_STYLE_BUILDERS.get(style)
        if builder is not None:
            thinking_extra_body = builder(thinking_enabled)
    if reasoning_effort is not None and is_kimi_thinking_model(resolved_name):
        fragment = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
        if thinking_extra_body:
            thinking_extra_body = {**thinking_extra_body, **fragment}
        else:
            thinking_extra_body = fragment

    # Empty reasoning_content echo is needed when thinking is actually on.
    style_thinking_on = (
        bool(style) and reasoning_effort is not None and thinking_enabled
    )
    kimi_thinking_on = (
        reasoning_effort is not None
        and is_kimi_thinking_model(resolved_name)
        and thinking_enabled
    )
    inject_empty_reasoning_content = style_thinking_on or kimi_thinking_on

    overrides: dict[str, Any] = {}
    if spec is not None:
        model_lower = resolved_name.lower()
        for pattern, spec_overrides in getattr(spec, "model_overrides", ()):
            if pattern in model_lower:
                overrides = dict(spec_overrides)
                break

    return ModelCompat(
        model_name=resolved_name,
        include_temperature=include_temperature,
        token_limit_field=token_limit_field,
        reasoning_effort_wire=reasoning_effort_wire,
        thinking_extra_body=thinking_extra_body,
        inject_empty_reasoning_content=inject_empty_reasoning_content,
        model_overrides=overrides,
    )
