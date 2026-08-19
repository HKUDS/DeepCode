"""P2-E4: LLMOps metric aggregation (GenAI lesson 14 five metrics)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability.llmops import aggregate_llmops

_RECORDS = [
    {
        "model": "m1",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "duration_ms": 100,
        "status": "ok",
    },
    {
        "model": "m1",
        "prompt_tokens": 2000,
        "completion_tokens": 1000,
        "duration_ms": 300,
        "status": "ok",
    },
    {
        "model": "m2",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "duration_ms": 50,
        "status": "error",
    },
]


def test_cost_from_tokens_with_default_prices(monkeypatch):
    monkeypatch.delenv("DEEPCODE_LLM_PRICES", raising=False)
    report = aggregate_llmops(_RECORDS)
    cost = report["cost"]
    # Total: (1000+2000+100) in = 3100, (500+1000+50) out = 1550.
    expected = 3100 / 1000 * 0.001 + 1550 / 1000 * 0.002
    assert abs(cost["usd"] - expected) < 1e-6
    assert cost["total_tokens"] == 4650
    assert cost["calls"] == 3


def test_cost_honors_custom_price_table(monkeypatch):
    monkeypatch.setenv("DEEPCODE_LLM_PRICES", "m1=0.01,0.02;m2=0.1,0.2")
    report = aggregate_llmops(_RECORDS)
    cost = report["cost"]
    expected = (
        3000 / 1000 * 0.01
        + 1500 / 1000 * 0.02  # m1
        + 100 / 1000 * 0.1
        + 50 / 1000 * 0.2  # m2
    )
    assert abs(cost["usd"] - expected) < 1e-6


def test_latency_percentiles():
    report = aggregate_llmops(_RECORDS)
    lat = report["latency"]
    assert lat["samples"] == 3
    assert lat["max_ms"] == 300
    assert lat["p50_ms"] == 100
    assert lat["p95_ms"] == 300


def test_status_counts():
    report = aggregate_llmops(_RECORDS)
    assert report["status"] == {"ok": 2, "error": 1}


def test_quality_harm_honesty_none_without_judge():
    report = aggregate_llmops(_RECORDS)
    assert report["quality"] is None
    assert report["honesty"] is None
    assert report["harm"] is None
    assert report["judged_samples"] == 0


def test_judge_fn_sampled_and_aggregated():
    calls = []

    def judge(record):
        calls.append(record["model"])
        return {"quality": 0.8, "harm": False, "honesty": 0.9}

    report = aggregate_llmops(_RECORDS, judge_fn=judge, sample_limit=2)
    assert len(calls) == 2  # sample_limit honored
    assert report["quality"] == 0.8
    assert report["honesty"] == 0.9
    assert report["harm"] == {"flagged": 0, "sampled": 2}
    assert report["judged_samples"] == 2


def test_judge_harm_flagged():
    def judge(record):
        return {"quality": 0.1, "harm": record["model"] == "m2", "honesty": 0.2}

    report = aggregate_llmops(_RECORDS, judge_fn=judge)
    assert report["harm"] == {"flagged": 1, "sampled": 3}


def test_empty_records():
    report = aggregate_llmops([])
    assert report["cost"]["calls"] == 0
    assert report["latency"]["samples"] == 0
