"""Model capability + cost lookup, backed by ``deepcode_config.json``.

This module owns the small per-model database used by retry / cost
calculations. The configured model name is read from
:class:`core.config.DeepCodeConfig` (lazily, via the runtime singleton)
so callers do not need to know where the JSON lives.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

from core.config import DeepCodeConfig


_UNKNOWN_MODELS_WARNED: Set[str] = set()


# Per-model capability database.
# Format: {model_name_pattern: {max_completion_tokens, max_context_tokens, cost_per_1m_input, cost_per_1m_output, provider}}
MODEL_LIMITS: Dict[str, Dict] = {
    # OpenAI
    "gpt-4o-mini": {
        "max_completion_tokens": 16384,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "provider": "openai",
    },
    "gpt-4o": {
        "max_completion_tokens": 16384,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 2.50,
        "output_cost_per_1m": 10.00,
        "provider": "openai",
    },
    "gpt-4-turbo": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 10.00,
        "output_cost_per_1m": 30.00,
        "provider": "openai",
    },
    "gpt-4": {
        "max_completion_tokens": 8192,
        "max_context_tokens": 8192,
        "input_cost_per_1m": 30.00,
        "output_cost_per_1m": 60.00,
        "provider": "openai",
    },
    "gpt-3.5-turbo": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 16385,
        "input_cost_per_1m": 0.50,
        "output_cost_per_1m": 1.50,
        "provider": "openai",
    },
    "o1-mini": {
        "max_completion_tokens": 65536,
        "max_context_tokens": 128000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 12.00,
        "provider": "openai",
    },
    "o1": {
        "max_completion_tokens": 100000,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 15.00,
        "output_cost_per_1m": 60.00,
        "provider": "openai",
    },
    # GPT-5 family. Substring lookup also catches Poe-routed aliases such as
    # ``gpt-5.4``. Per-token costs left at 0.0 because Poe / proxy gateways
    # bill per-message, so downstream cost reports show $0 instead of guessing.
    "gpt-5": {
        "max_completion_tokens": 32768,
        "max_context_tokens": 400000,
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "provider": "openai",
    },
    # Anthropic
    "claude-3-5-sonnet": {
        "max_completion_tokens": 8192,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "provider": "anthropic",
    },
    "claude-3-opus": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 15.00,
        "output_cost_per_1m": 75.00,
        "provider": "anthropic",
    },
    "claude-3-sonnet": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "provider": "anthropic",
    },
    "claude-3-haiku": {
        "max_completion_tokens": 4096,
        "max_context_tokens": 200000,
        "input_cost_per_1m": 0.25,
        "output_cost_per_1m": 1.25,
        "provider": "anthropic",
    },
}


def _resolve_config(config: DeepCodeConfig | None = None) -> DeepCodeConfig | None:
    if config is not None:
        return config
    try:
        from core.compat.runtime import get_runtime

        return get_runtime().config
    except Exception:
        return None


def get_model_from_config(
    config: DeepCodeConfig | None = None, *, phase: str = "default"
) -> Optional[str]:
    """Return the resolved model for *phase* from the runtime config."""
    cfg = _resolve_config(config)
    if cfg is None:
        return None
    try:
        return cfg.resolve_phase(phase).model
    except Exception:
        return None


def get_model_limits(
    model_name: Optional[str] = None,
    config: DeepCodeConfig | None = None,
    *,
    phase: str = "default",
) -> Dict:
    """Return the capability/cost record for *model_name*."""
    if not model_name:
        model_name = get_model_from_config(config, phase=phase)

    if not model_name:
        print("⚠️ Warning: Could not determine model, using safe defaults")
        return {
            "max_completion_tokens": 4096,
            "max_context_tokens": 8192,
            "input_cost_per_1m": 1.00,
            "output_cost_per_1m": 3.00,
            "provider": "unknown",
        }

    # Prefer the longest matching pattern so ``gpt-5.4`` matches the
    # ``gpt-5`` family entry rather than no entry, and ``gpt-4o`` matches
    # ``gpt-4o`` rather than the generic ``gpt-4``.
    best_pattern: Optional[str] = None
    best_limits: Optional[Dict] = None
    lowered = model_name.lower()
    for pattern, limits in MODEL_LIMITS.items():
        if pattern.lower() in lowered:
            if best_pattern is None or len(pattern) > len(best_pattern):
                best_pattern = pattern
                best_limits = limits
    if best_limits is not None:
        return best_limits.copy()

    # Warn at most once per unknown model so a long-running pipeline does
    # not spam dozens of identical messages.
    if model_name not in _UNKNOWN_MODELS_WARNED:
        _UNKNOWN_MODELS_WARNED.add(model_name)
        print(
            f"⚠️ Warning: Model '{model_name}' not in database, using conservative "
            "defaults (this warning will not repeat for this model)"
        )
    return {
        "max_completion_tokens": 4096,
        "max_context_tokens": 8192,
        "input_cost_per_1m": 1.00,
        "output_cost_per_1m": 3.00,
        "provider": "unknown",
    }


def get_safe_max_tokens(
    model_name: Optional[str] = None,
    config: DeepCodeConfig | None = None,
    safety_margin: float = 0.9,
) -> int:
    """Return ``int(max_completion_tokens * safety_margin)`` for *model_name*."""
    limits = get_model_limits(model_name, config)
    safe_tokens = int(limits["max_completion_tokens"] * safety_margin)
    print(
        f"🔧 Safe max_tokens for {model_name or 'current model'}: {safe_tokens} "
        f"({safety_margin*100:.0f}% of {limits['max_completion_tokens']})"
    )
    return safe_tokens


def calculate_token_cost(
    input_tokens: int,
    output_tokens: int,
    model_name: Optional[str] = None,
    config: DeepCodeConfig | None = None,
) -> float:
    """Return the dollar cost of *input_tokens* + *output_tokens* on *model_name*."""
    limits = get_model_limits(model_name, config)
    input_cost = (input_tokens / 1_000_000) * limits["input_cost_per_1m"]
    output_cost = (output_tokens / 1_000_000) * limits["output_cost_per_1m"]
    return input_cost + output_cost


def get_retry_token_limits(
    base_tokens: int,
    retry_count: int,
    model_name: Optional[str] = None,
    config: DeepCodeConfig | None = None,
) -> int:
    """Return retry-adjusted token limits, capped at the model maximum."""
    limits = get_model_limits(model_name, config)
    max_allowed = limits["max_completion_tokens"]

    if retry_count == 0:
        new_tokens = int(max_allowed * 0.875)
    elif retry_count == 1:
        new_tokens = int(max_allowed * 0.95)
    else:
        new_tokens = int(max_allowed * 0.98)

    new_tokens = min(new_tokens, max_allowed)
    print(
        f"🔧 Retry {retry_count + 1}: Adjusting tokens from {base_tokens} → {new_tokens} "
        f"(max: {max_allowed})"
    )
    return new_tokens


def get_provider_from_model(
    model_name: Optional[str] = None, config: DeepCodeConfig | None = None
) -> str:
    """Return the provider label associated with *model_name* in MODEL_LIMITS."""
    limits = get_model_limits(model_name, config)
    return limits.get("provider", "unknown")
