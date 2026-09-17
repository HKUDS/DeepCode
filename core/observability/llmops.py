"""Cost and latency summary over recorded LLM calls.

``llm.jsonl`` already holds one :class:`~core.observability.records.LLMLogRecord`
per model call — provider, model, token usage, duration and status — but
nothing in the runtime turned that into a number an operator can act on.
This module does exactly that and nothing more:

* **Cost** is computed from the token usage in each record and the list price
  the model catalog knows (:func:`core.providers.catalog.resolve_model_info`,
  USD per 1M tokens). A model without a catalog price contributes its tokens
  but no dollars, and is counted in ``unpriced_calls`` so the total is never
  silently understated.
* **Latency** is reported as p50 / p95 / max over ``duration_ms``.
* **Status** counts ``ok`` / ``error`` / ``retry`` as recorded.

Pure over record dicts (the ``to_jsonl`` shape); the CLI entry point reads a
JSONL file::

    python -m core.observability.llmops ~/.deepcode/logs/llm.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.providers.catalog import resolve_model_info

__all__ = ["LLMCallSummary", "load_llm_records", "main", "summarize_llm_calls"]


@dataclass
class _ModelBucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    usd: float | None = 0.0
    unpriced_calls: int = 0


@dataclass
class LLMCallSummary:
    """What :func:`summarize_llm_calls` returns."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    usd: float | None
    unpriced_calls: int
    latency_ms: dict[str, int | None]
    status: dict[str, int]
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "cached": self.cached_tokens,
                "total": self.prompt_tokens + self.completion_tokens,
            },
            "cost": {"usd": self.usd, "unpriced_calls": self.unpriced_calls},
            "latency_ms": self.latency_ms,
            "status": self.status,
            "by_model": self.by_model,
        }


def _int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * q))
    return ordered[index]


def summarize_llm_calls(records: Iterable[dict[str, Any]]) -> LLMCallSummary:
    """Aggregate LLM log records into cost, latency and status figures.

    Prices come from the model catalog. When a model has no list price the
    call's tokens are still counted, its dollars are not, and the call is
    added to ``unpriced_calls``; if *every* call is unpriced ``usd`` is
    ``None`` rather than a misleading zero.
    """
    calls = 0
    prompt_total = completion_total = cached_total = 0
    usd_total = 0.0
    priced_any = False
    unpriced = 0
    latencies: list[int] = []
    status: dict[str, int] = {}
    buckets: dict[str, _ModelBucket] = {}
    price_cache: dict[str, tuple[float | None, float | None]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        calls += 1
        model = str(record.get("model") or "unknown")
        prompt = _int(record.get("prompt_tokens"))
        completion = _int(record.get("completion_tokens"))
        cached = _int(record.get("cached_tokens"))
        prompt_total += prompt
        completion_total += completion
        cached_total += cached

        if model not in price_cache:
            info = resolve_model_info(model)
            price_cache[model] = (info.input_cost_per_1m, info.output_cost_per_1m)
        price_in, price_out = price_cache[model]
        bucket = buckets.setdefault(model, _ModelBucket())
        bucket.calls += 1
        bucket.prompt_tokens += prompt
        bucket.completion_tokens += completion
        bucket.cached_tokens += cached
        if price_in is None or price_out is None:
            unpriced += 1
            bucket.unpriced_calls += 1
            bucket.usd = None
        else:
            cost = prompt / 1_000_000 * price_in + completion / 1_000_000 * price_out
            usd_total += cost
            priced_any = True
            if bucket.usd is not None:
                bucket.usd += cost

        duration = record.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            latencies.append(int(duration))
        state = str(record.get("status") or "unknown")
        status[state] = status.get(state, 0) + 1

    latency: dict[str, int | None] = {
        "samples": len(latencies),
        "p50": int(statistics.median(latencies)) if latencies else None,
        "p95": _percentile(latencies, 0.95) if latencies else None,
        "max": max(latencies) if latencies else None,
    }
    by_model = {
        name: {
            "calls": b.calls,
            "prompt_tokens": b.prompt_tokens,
            "completion_tokens": b.completion_tokens,
            "cached_tokens": b.cached_tokens,
            "usd": None if b.usd is None else round(b.usd, 6),
            "unpriced_calls": b.unpriced_calls,
        }
        for name, b in sorted(buckets.items())
    }
    return LLMCallSummary(
        calls=calls,
        prompt_tokens=prompt_total,
        completion_tokens=completion_total,
        cached_tokens=cached_total,
        usd=round(usd_total, 6) if priced_any else None,
        unpriced_calls=unpriced,
        latency_ms=latency,
        status=status,
        by_model=by_model,
    )


def load_llm_records(path: str | Path) -> list[dict[str, Any]]:
    """Read one ``llm.jsonl`` file; unparseable lines are skipped."""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _format_text(summary: LLMCallSummary) -> str:
    usd = "n/a" if summary.usd is None else f"${summary.usd:.4f}"
    lines = [
        f"calls: {summary.calls}   status: "
        + ", ".join(f"{k}={v}" for k, v in sorted(summary.status.items())),
        f"tokens: prompt={summary.prompt_tokens} completion={summary.completion_tokens} "
        f"cached={summary.cached_tokens}",
        f"cost: {usd} (unpriced calls: {summary.unpriced_calls})",
        f"latency ms: p50={summary.latency_ms['p50']} p95={summary.latency_ms['p95']} "
        f"max={summary.latency_ms['max']}",
    ]
    for name, b in summary.by_model.items():
        model_usd = "n/a" if b["usd"] is None else f"${b['usd']:.4f}"
        lines.append(
            f"  {name}: calls={b['calls']} prompt={b['prompt_tokens']} "
            f"completion={b['completion_tokens']} usd={model_usd}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.observability.llmops",
        description="Summarise cost, latency and status over an llm.jsonl file.",
    )
    parser.add_argument("path", help="Path to an llm.jsonl file")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)
    try:
        records = load_llm_records(args.path)
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 1
    summary = summarize_llm_calls(records)
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_text(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
