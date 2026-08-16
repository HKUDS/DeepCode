"""P2-E4 (GenAI lesson 14): LLMOps metric aggregation.

Lesson 14's five LLMOps metrics: **Quality / Harm / Honesty / Cost /
Latency**. DeepCode already records per-call LLM/MCP logs
(``core.observability.records``); this module aggregates them into the five
dimensions:

* **Cost** — from token usage × a pluggable per-model price table (USD).
* **Latency** — from recorded durations (ms), p50/p95/max.
* **Quality / Harm / Honesty** — machine-observable proxies + an optional
  LLM-as-judge hook (``judge_fn``) for sampled labels; without a judge these
  stay ``None`` (unmeasured) rather than fabricated.

Pure mechanism: it consumes a list of record dicts (the ``to_jsonl`` shape)
and returns a summary dict. No I/O, no network.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from typing import Any

# Default per-1K-token prices (USD) — conservative ballpark so cost is
# meaningful even without a configured table. Override via
# DEEPCODE_PRICE_PER_1K_IN / _OUT or a caller-supplied table.
_DEFAULT_PRICE_IN = 0.001  # $ per 1K input tokens
_DEFAULT_PRICE_OUT = 0.002  # $ per 1K output tokens


def _price_table() -> dict[str, tuple[float, float]]:
    """(input $/1K, output $/1K) per model; '' = default for unknown."""
    import os

    table: dict[str, tuple[float, float]] = {}
    raw = os.environ.get("DEEPCODE_LLM_PRICES", "").strip()
    # Format: "model=in,out;model2=in,out"
    for part in raw.split(";"):
        if not part.strip():
            continue
        model, _, prices = part.partition("=")
        try:
            pin, pout = (float(x) for x in prices.split(","))
        except ValueError:
            continue
        table[model.strip()] = (pin, pout)
    return table


def _prices_for(model: str | None, table: dict[str, tuple[float, float]]) -> tuple[float, float]:
    if model and model in table:
        return table[model]
    try:
        return _price_table().get(model, (_DEFAULT_PRICE_IN, _DEFAULT_PRICE_OUT))
    except Exception:  # noqa: BLE001
        return (_DEFAULT_PRICE_IN, _DEFAULT_PRICE_OUT)


def aggregate_llmops(
    records: Iterable[dict[str, Any]],
    *,
    judge_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Aggregate LLM log records into the five LLMOps dimensions.

    Parameters
    ----------
    records:
        Iterable of LLM log record dicts (the ``to_jsonl`` shape: ``model``,
        ``prompt_tokens``, ``completion_tokens``, ``duration_ms``, ``status``,
        ``finish_reason``).
    judge_fn:
        Optional ``(record) -> {"quality": 0-1, "harm": bool, "honesty": 0-1}``
        sampler; applied to at most ``sample_limit`` records. Without it,
        quality/harm/honesty remain ``None``.
    sample_limit:
        Max records handed to ``judge_fn`` (cost control, lesson 14).

    Returns a dict with keys ``quality / harm / honesty / cost / latency``.
    """
    records = [r for r in records if isinstance(r, dict)]
    total_tokens = 0
    total_cost = 0.0
    latencies: list[int] = []
    errors = 0
    ok = 0
    table = _price_table()

    for record in records:
        model = record.get("model")
        pin, pout = _prices_for(model, table)
        prompt = int(record.get("prompt_tokens") or 0)
        completion = int(record.get("completion_tokens") or 0)
        total_tokens += prompt + completion
        total_cost += prompt / 1000 * pin + completion / 1000 * pout
        duration = record.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            latencies.append(int(duration))
        if record.get("status") == "error":
            errors += 1
        else:
            ok += 1

    latency_summary: dict[str, Any] = {
        "samples": len(latencies),
        "max_ms": max(latencies) if latencies else None,
    }
    if latencies:
        latency_summary["p50_ms"] = int(statistics.median(latencies))
        latency_summary["p95_ms"] = _percentile(latencies, 0.95)

    judged: list[dict[str, Any]] = []
    if judge_fn is not None:
        for record in records[:sample_limit]:
            try:
                verdict = judge_fn(record)
            except Exception:  # noqa: BLE001 - a judge failure is a skipped sample
                verdict = None
            if verdict:
                judged.append(verdict)

    quality = _mean(judged, "quality")
    honesty = _mean(judged, "honesty")
    harm_count = sum(1 for v in judged if v.get("harm"))
    harm = (
        {"flagged": harm_count, "sampled": len(judged)}
        if judged
        else None
    )

    return {
        "quality": quality,
        "harm": harm,
        "honesty": honesty,
        "cost": {
            "usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "calls": len(records),
        },
        "latency": latency_summary,
        "status": {"ok": ok, "error": errors},
        "judged_samples": len(judged),
    }


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * q))
    return ordered[index]


def _mean(judged: list[dict[str, Any]], key: str) -> float | None:
    values = [v[key] for v in judged if isinstance(v.get(key), (int, float))]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


__all__ = ["aggregate_llmops"]
