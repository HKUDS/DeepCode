"""Cost/latency summary over llm.jsonl records."""

from __future__ import annotations

import json
from pathlib import Path

from core.observability.llmops import (
    load_llm_records,
    main,
    summarize_llm_calls,
)
from core.providers.catalog import resolve_model_info

PRICED = "deepseek-chat"  # priced in the seed catalog
UNPRICED = "totally-unknown-model-xyz"


def _record(model: str, prompt: int, completion: int, ms: int, status: str = "ok"):
    return {
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": 0,
        "duration_ms": ms,
        "status": status,
    }


def test_cost_uses_catalog_list_prices():
    info = resolve_model_info(PRICED)
    assert info.input_cost_per_1m is not None and info.output_cost_per_1m is not None
    summary = summarize_llm_calls([_record(PRICED, 1_000_000, 1_000_000, 100)])
    assert summary.usd == round(info.input_cost_per_1m + info.output_cost_per_1m, 6)
    assert summary.unpriced_calls == 0


def test_unpriced_models_count_tokens_but_not_dollars():
    summary = summarize_llm_calls([_record(UNPRICED, 10, 5, 50)])
    assert summary.prompt_tokens == 10 and summary.completion_tokens == 5
    assert summary.usd is None and summary.unpriced_calls == 1
    assert summary.by_model[UNPRICED]["usd"] is None


def test_mixed_models_keep_priced_total_and_flag_the_rest():
    summary = summarize_llm_calls(
        [_record(PRICED, 1000, 1000, 10), _record(UNPRICED, 1000, 1000, 20)]
    )
    assert summary.usd is not None and summary.usd > 0
    assert summary.unpriced_calls == 1
    assert set(summary.by_model) == {PRICED, UNPRICED}


def test_latency_and_status_are_summarised():
    records = [_record(PRICED, 1, 1, ms) for ms in (10, 20, 30, 40, 1000)]
    records.append(_record(PRICED, 1, 1, 5, status="error"))
    summary = summarize_llm_calls(records)
    assert summary.calls == 6
    assert summary.status == {"ok": 5, "error": 1}
    assert summary.latency_ms["p50"] == 25 and summary.latency_ms["max"] == 1000
    assert summary.latency_ms["p95"] == 1000


def test_malformed_records_are_ignored():
    summary = summarize_llm_calls(
        [None, "x", {"model": PRICED, "prompt_tokens": "bad"}]
    )
    assert summary.calls == 1 and summary.prompt_tokens == 0


def test_cli_reads_jsonl_and_skips_bad_lines(tmp_path: Path, capsys):
    path = tmp_path / "llm.jsonl"
    path.write_text(
        json.dumps(_record(PRICED, 100, 50, 12)) + "\nnot json\n\n", encoding="utf-8"
    )
    assert len(load_llm_records(path)) == 1
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["calls"] == 1 and payload["tokens"]["prompt"] == 100
    assert main([str(path)]) == 0
    assert "calls: 1" in capsys.readouterr().out


def test_cli_reports_a_missing_file(tmp_path: Path, capsys):
    assert main([str(tmp_path / "nope.jsonl")]) == 1
    assert "cannot read" in capsys.readouterr().err
