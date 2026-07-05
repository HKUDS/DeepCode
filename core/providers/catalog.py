"""Per-model metadata catalog — context window, output cap, pricing (P2, L0).

Why this exists
---------------
The runner's context-governance ladder (``AgentRunner._snip_history`` /
``_microcompact``) only activates when it knows the model's **context
window** — the token budget it must keep the prompt under. Until now that
number came from a single global ``AgentDefaults.context_window_tokens``
(65 536, DEEPCODE_V2_MASTER_PLAN.md §7 P2), the same value for a 400 K-window
GPT-5 and a 128 K DeepSeek. That is both wasteful (a huge model trimmed to
64 K) and unsafe (a small model never trimmed until the provider 400s).

This module is the *mechanism* half of the fix: a deterministic, offline
lookup ``model_id -> ModelInfo``. The *decision* half — how aggressively to
compact — stays in the runner. No network call sits on the hot path; the
built-in seed is authoritative and a models.dev export can be merged in
out-of-band via :func:`load_catalog_snapshot`.

Shape mirrors the `models.dev <https://models.dev>`_ schema so a snapshot of
their catalog maps in field-for-field: ``limit.context`` → ``context_window``,
``limit.output`` → ``max_output_tokens``, ``cost.input`` / ``cost.output`` →
USD per 1M tokens.

Anti-hardcoding (§3.4, §8 #15): resolution is a normalize → exact → family
→ default cascade over declarative tables, never ``if "gpt-5" in name``
scattered through call sites. A model the seed has never seen (``gpt-5.6``)
still resolves through its family rule instead of silently taking the wrong
global default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Resolved metadata for one model id.

    ``context_window`` is the total token budget the model accepts (input +
    output). ``max_output_tokens`` is the largest completion it will emit.
    Costs are USD per 1M tokens, or ``None`` when unknown (e.g. a local or
    unpriced model). ``source`` records where the value came from — ``seed``,
    ``family:<prefix>``, ``default``, or ``snapshot`` — so a surprising
    compaction budget is traceable to its origin.
    """

    id: str
    context_window: int
    max_output_tokens: int
    input_cost_per_1m: float | None = None
    output_cost_per_1m: float | None = None
    source: str = "seed"


# --------------------------------------------------------------------------
# Seed catalog — exact ids DeepCode targets, curated from models.dev.
# Context/output are the vendor-published limits; costs are list price per 1M.
# Conservative when a vendor advertises a beta-only larger window (we take the
# generally-available number) — under-estimating the window only makes
# compaction trigger a little earlier, which is safe; over-estimating risks a
# hard context-overflow error, which is not.
# --------------------------------------------------------------------------
_SEED: dict[str, ModelInfo] = {
    # OpenAI GPT-5 family (400K context / 128K output).
    "gpt-5": ModelInfo("gpt-5", 400_000, 128_000, 1.25, 10.0),
    "gpt-5-mini": ModelInfo("gpt-5-mini", 400_000, 128_000, 0.25, 2.0),
    "gpt-5-nano": ModelInfo("gpt-5-nano", 400_000, 128_000, 0.05, 0.40),
    "gpt-5.1": ModelInfo("gpt-5.1", 400_000, 128_000, 1.25, 10.0),
    "gpt-5.2": ModelInfo("gpt-5.2", 400_000, 128_000, 1.25, 10.0),
    "gpt-5.4": ModelInfo("gpt-5.4", 400_000, 128_000, 1.25, 10.0),
    # OpenAI reasoning o-series (200K context / 100K output).
    "o1": ModelInfo("o1", 200_000, 100_000, 15.0, 60.0),
    "o3": ModelInfo("o3", 200_000, 100_000, 2.0, 8.0),
    "o3-mini": ModelInfo("o3-mini", 200_000, 100_000, 1.1, 4.4),
    "o4-mini": ModelInfo("o4-mini", 200_000, 100_000, 1.1, 4.4),
    # OpenAI GPT-4 family.
    "gpt-4o": ModelInfo("gpt-4o", 128_000, 16_384, 2.5, 10.0),
    "gpt-4o-mini": ModelInfo("gpt-4o-mini", 128_000, 16_384, 0.15, 0.60),
    "gpt-4.1": ModelInfo("gpt-4.1", 1_047_576, 32_768, 2.0, 8.0),
    # Anthropic Claude (200K context; output varies by tier).
    "claude-opus-4-1": ModelInfo("claude-opus-4-1", 200_000, 32_000, 15.0, 75.0),
    "claude-opus-4-8": ModelInfo("claude-opus-4-8", 200_000, 32_000, 15.0, 75.0),
    "claude-sonnet-4-5": ModelInfo("claude-sonnet-4-5", 200_000, 64_000, 3.0, 15.0),
    "claude-sonnet-5": ModelInfo("claude-sonnet-5", 200_000, 64_000, 3.0, 15.0),
    "claude-haiku-4-5": ModelInfo("claude-haiku-4-5", 200_000, 64_000, 1.0, 5.0),
    # Google Gemini (very large windows).
    "gemini-2.5-pro": ModelInfo("gemini-2.5-pro", 1_048_576, 65_536, 1.25, 10.0),
    "gemini-2.5-flash": ModelInfo("gemini-2.5-flash", 1_048_576, 65_536, 0.30, 2.5),
    "gemini-3-pro": ModelInfo("gemini-3-pro", 1_048_576, 65_536, 1.25, 10.0),
    # Moonshot Kimi.
    "kimi-k2": ModelInfo("kimi-k2", 256_000, 128_000, 0.60, 2.5),
    "kimi-k2.5": ModelInfo("kimi-k2.5", 256_000, 128_000, 0.60, 2.5),
    "kimi-k2.6": ModelInfo("kimi-k2.6", 256_000, 128_000, 0.60, 2.5),
    # DeepSeek.
    "deepseek-v3": ModelInfo("deepseek-v3", 128_000, 8_192, 0.27, 1.10),
    "deepseek-r1": ModelInfo("deepseek-r1", 128_000, 65_536, 0.55, 2.19),
    # Alibaba Qwen.
    "qwen3-max": ModelInfo("qwen3-max", 256_000, 32_768, 1.2, 6.0),
    "qwen3-coder": ModelInfo("qwen3-coder", 256_000, 65_536, 1.0, 5.0),
    # xAI Grok.
    "grok-4": ModelInfo("grok-4", 256_000, 64_000, 3.0, 15.0),
}


# Family rules: ordered (specific → general) prefix match for ids the seed
# doesn't list exactly, so a new point release inherits its family's limits.
# Each entry is (bare-id prefix, template ModelInfo). Order matters — the
# first matching prefix wins, so "gpt-5" precedes "gpt-4".
_FAMILY_RULES: tuple[tuple[str, ModelInfo], ...] = (
    ("gpt-5", _SEED["gpt-5"]),
    ("gpt-4.1", _SEED["gpt-4.1"]),
    ("gpt-4", _SEED["gpt-4o"]),
    ("o1", _SEED["o1"]),
    ("o3", _SEED["o3"]),
    ("o4", _SEED["o4-mini"]),
    ("claude-opus", _SEED["claude-opus-4-8"]),
    ("claude-sonnet", _SEED["claude-sonnet-5"]),
    ("claude-haiku", _SEED["claude-haiku-4-5"]),
    ("claude", _SEED["claude-sonnet-5"]),
    ("gemini-3", _SEED["gemini-3-pro"]),
    ("gemini", _SEED["gemini-2.5-pro"]),
    ("kimi", _SEED["kimi-k2.6"]),
    ("deepseek-r", _SEED["deepseek-r1"]),
    ("deepseek", _SEED["deepseek-v3"]),
    ("qwen", _SEED["qwen3-max"]),
    ("grok", _SEED["grok-4"]),
)

# Conservative fallback for a genuinely unknown model: 128K is the smallest
# window any mainstream 2025+ coding model ships, so it never over-promises.
_DEFAULT = ModelInfo("unknown", 128_000, 8_192, None, None, source="default")

# Snapshot overrides merged in via load_catalog_snapshot(); consulted before
# the seed so an operator can refresh numbers without editing this file.
_SNAPSHOT: dict[str, ModelInfo] = {}


def _normalize(model_id: str) -> str:
    """Bare, lowercased model id: strip a provider prefix and whitespace.

    ``openai/gpt-5.4`` → ``gpt-5.4``; ``  Claude-Sonnet-5 `` → ``claude-sonnet-5``.
    """
    bare = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id
    return bare.strip().lower()


def resolve_model_info(model_id: str | None) -> ModelInfo:
    """Resolve metadata for ``model_id`` via normalize → snapshot → seed →
    family → default. Always returns a value (never raises); the ``source``
    field records which rung matched.
    """
    if not model_id:
        return _DEFAULT
    key = _normalize(model_id)
    snap = _SNAPSHOT.get(key)
    if snap is not None:
        return snap
    seed = _SEED.get(key)
    if seed is not None:
        return seed
    for prefix, template in _FAMILY_RULES:
        if key.startswith(prefix):
            return replace(template, id=key, source=f"family:{prefix}")
    return replace(_DEFAULT, id=key)


def context_window_for(model_id: str | None) -> int:
    """The model's total context-window budget in tokens (input + output)."""
    return resolve_model_info(model_id).context_window


def max_output_tokens_for(model_id: str | None) -> int:
    """The model's maximum completion length in tokens."""
    return resolve_model_info(model_id).max_output_tokens


def _coerce_info(model_id: str, entry: dict[str, Any]) -> ModelInfo | None:
    """Build a :class:`ModelInfo` from one models.dev-shaped JSON entry.

    Accepts either the nested models.dev shape (``limit.context`` /
    ``limit.output`` / ``cost.input`` / ``cost.output``) or a flat shape
    (``context_window`` / ``max_output_tokens`` / ``input_cost_per_1m`` /
    ``output_cost_per_1m``). Returns ``None`` if no context window is present.
    """
    limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else {}
    cost = entry.get("cost") if isinstance(entry.get("cost"), dict) else {}
    context = entry.get("context_window", limit.get("context"))
    if not context:
        return None
    output = entry.get("max_output_tokens", limit.get("output")) or 4096
    return ModelInfo(
        id=_normalize(model_id),
        context_window=int(context),
        max_output_tokens=int(output),
        input_cost_per_1m=entry.get("input_cost_per_1m", cost.get("input")),
        output_cost_per_1m=entry.get("output_cost_per_1m", cost.get("output")),
        source="snapshot",
    )


def load_catalog_snapshot(path: str | Path) -> int:
    """Merge a models.dev-style JSON snapshot into the resolver.

    The file may be a ``{model_id: entry}`` map or a ``{"models": {...}}`` /
    list-of-entries wrapper. Entries without a context window are skipped.
    Returns the count merged. Runs off the hot path (call once at startup or
    from a refresh command) — the seed remains the offline default.
    """
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict) and "models" in data:
        data = data["models"]
    items: list[tuple[str, dict[str, Any]]]
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, list):
        items = [(e.get("id", ""), e) for e in data if isinstance(e, dict)]
    else:
        return 0
    merged = 0
    for model_id, entry in items:
        if not model_id or not isinstance(entry, dict):
            continue
        info = _coerce_info(model_id, entry)
        if info is not None:
            _SNAPSHOT[info.id] = info
            merged += 1
    return merged


def known_model_ids() -> list[str]:
    """Every exactly-known id (snapshot ∪ seed), sorted — for diagnostics/UI."""
    return sorted(set(_SNAPSHOT) | set(_SEED))
