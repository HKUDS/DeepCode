"""Tests for the paired harness-measurement ledger (SoL-Pi two-gate port)."""

from __future__ import annotations

import json

import pytest

from core.agent_runtime.harness_metrics import (
    CapabilityOutcome,
    EfficiencySample,
    HarnessLedger,
    HarnessMetricsError,
    HarnessRecord,
    ModelIdentity,
    judge_pair,
)

_MODEL = ModelIdentity(provider="deepseek", model="deepseek-v4-flash", effort="high")


def _record(
    *,
    variant: str,
    score: float | None = 1.0,
    passed: bool = True,
    tokens_hit: int = 0,
    tokens_miss: int = 1000,
    cost: float | None = None,
    activated: bool = True,
    task_id: str = "task-1",
    mechanism: str = "observation_pack",
    model: ModelIdentity | None = None,
) -> HarnessRecord:
    return HarnessRecord(
        mechanism=mechanism,
        task_id=task_id,
        variant=variant,
        model=model or _MODEL,
        capability=CapabilityOutcome(passed=passed, score=score),
        efficiency=EfficiencySample(
            tokens_cache_hit=tokens_hit, tokens_cache_miss=tokens_miss, cost_usd=cost
        ),
        activated=activated,
    )


# --------------------------------------------------------------------------
# Gate behaviour
# --------------------------------------------------------------------------


def test_accepts_when_capability_holds_and_tokens_drop():
    decision = judge_pair(
        _record(variant="base"), _record(variant="candidate", tokens_miss=500)
    )
    assert decision.accepted
    assert decision.capability_ok and decision.efficiency_ok
    assert decision.activation_ok and decision.identity_ok
    assert decision.deltas["tokens_base"] == 1000
    assert decision.deltas["tokens_candidate"] == 500


def test_rejects_capability_regression_beyond_tolerance():
    decision = judge_pair(
        _record(variant="base", score=1.0),
        _record(variant="candidate", score=0.8, tokens_miss=100),
    )
    assert not decision.accepted
    assert not decision.capability_ok
    assert any("REJECT capability" in reason for reason in decision.reasons)


def test_tolerance_admits_bounded_capability_loss():
    """SoL-Pi keeps ~94% of the base score; the tolerance is the predeclared knob."""
    decision = judge_pair(
        _record(variant="base", score=1.0),
        _record(variant="candidate", score=0.94, tokens_miss=500),
        capability_tolerance=0.06,
    )
    assert decision.capability_ok
    assert decision.accepted


def test_rejects_without_any_efficiency_gain():
    decision = judge_pair(
        _record(variant="base", tokens_miss=1000),
        _record(variant="candidate", tokens_miss=1000),
    )
    assert not decision.accepted
    assert not decision.efficiency_ok
    assert any("REJECT efficiency" in reason for reason in decision.reasons)


def test_min_token_gain_fraction_is_enforced():
    decision = judge_pair(
        _record(variant="base", tokens_miss=1000),
        _record(variant="candidate", tokens_miss=990),
        min_token_gain_fraction=0.05,
    )
    assert not decision.efficiency_ok


def test_cost_improvement_alone_satisfies_efficiency_gate():
    decision = judge_pair(
        _record(variant="base", tokens_miss=1000, cost=2.0),
        _record(variant="candidate", tokens_miss=1000, cost=1.0),
    )
    assert decision.efficiency_ok
    assert decision.accepted


def test_rejects_when_mechanism_never_fired():
    """M15: 'we enabled it and nothing broke' is not a win."""
    decision = judge_pair(
        _record(variant="base"),
        _record(variant="candidate", tokens_miss=500, activated=False),
    )
    assert not decision.accepted
    assert not decision.activation_ok
    assert any("REJECT activation" in reason for reason in decision.reasons)


def test_rejects_pair_across_different_models():
    """R13: two models are not a pair."""
    other = ModelIdentity(provider="anthropic", model="claude-x", effort="high")
    decision = judge_pair(
        _record(variant="base"),
        _record(variant="candidate", tokens_miss=100, model=other),
    )
    assert not decision.accepted
    assert not decision.identity_ok


def test_effort_difference_also_breaks_identity():
    other = ModelIdentity(provider="deepseek", model="deepseek-v4-flash", effort="low")
    decision = judge_pair(
        _record(variant="base"),
        _record(variant="candidate", tokens_miss=100, model=other),
    )
    assert not decision.identity_ok


def test_unknown_score_fails_closed():
    """M30: an ambiguous terminal state is a rejection, not a pass."""
    decision = judge_pair(
        _record(variant="base", score=None),
        _record(variant="candidate", score=None, tokens_miss=100),
    )
    assert not decision.accepted
    assert not decision.capability_ok
    assert any("M30" in reason for reason in decision.reasons)


def test_candidate_that_did_not_pass_is_never_accepted():
    decision = judge_pair(
        _record(variant="base"),
        _record(variant="candidate", passed=False, tokens_miss=100),
    )
    assert not decision.accepted
    assert not decision.capability_ok


def test_pairing_rejects_two_records_from_the_same_arm():
    with pytest.raises(HarnessMetricsError, match="one base and one candidate"):
        judge_pair(_record(variant="base"), _record(variant="base"))


def test_pairing_rejects_different_tasks():
    with pytest.raises(HarnessMetricsError, match="different experiments"):
        judge_pair(
            _record(variant="base", task_id="a"),
            _record(variant="candidate", task_id="b"),
        )


def test_argument_order_does_not_matter():
    base = _record(variant="base")
    candidate = _record(variant="candidate", tokens_miss=500)
    assert judge_pair(base, candidate).accepted
    assert judge_pair(candidate, base).accepted


def test_negative_tolerance_is_rejected():
    with pytest.raises(HarnessMetricsError, match="non-negative"):
        judge_pair(
            _record(variant="base"),
            _record(variant="candidate"),
            capability_tolerance=-1,
        )


# --------------------------------------------------------------------------
# Validation at the edges
# --------------------------------------------------------------------------


def test_efficiency_rejects_negative_counters():
    with pytest.raises(HarnessMetricsError, match="non-negative"):
        EfficiencySample(tokens_cache_miss=-1)


def test_model_identity_requires_provider_and_model():
    with pytest.raises(HarnessMetricsError, match="non-empty provider and model"):
        ModelIdentity(provider="  ", model="m")


def test_from_dict_rejects_unknown_keys():
    payload = _record(variant="base").to_dict()
    payload["surprise"] = 1
    with pytest.raises(HarnessMetricsError, match="unknown key"):
        HarnessRecord.from_dict(payload)


def test_from_dict_rejects_missing_nested_object():
    payload = _record(variant="base").to_dict()
    del payload["efficiency"]
    with pytest.raises(HarnessMetricsError, match="efficiency must be an object"):
        HarnessRecord.from_dict(payload)


def test_from_dict_rejects_wrong_scalar_type():
    payload = _record(variant="base").to_dict()
    payload["efficiency"]["turns"] = "many"
    with pytest.raises(HarnessMetricsError, match="turns must be an integer"):
        HarnessRecord.from_dict(payload)


def test_roundtrip_through_dict_is_lossless():
    original = HarnessRecord(
        mechanism="observation_pack",
        task_id="t",
        variant="candidate",
        model=_MODEL,
        capability=CapabilityOutcome(passed=True, score=0.9, detail="5/5 checks"),
        efficiency=EfficiencySample(
            tokens_cache_hit=10,
            tokens_cache_miss=20,
            cost_usd=0.5,
            turns=3,
            tool_calls=7,
            wall_seconds=1.5,
        ),
        activated=True,
        notes="first firing",
    )
    assert HarnessRecord.from_dict(original.to_dict()) == original


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


def test_ledger_roundtrip_preserves_append_order(tmp_path):
    ledger = HarnessLedger(tmp_path / "ledger.jsonl")
    base = _record(variant="base")
    candidate = _record(variant="candidate", tokens_miss=500)
    ledger.append(base)
    ledger.append(candidate)
    assert ledger.read_all() == [base, candidate]


def test_ledger_missing_file_reads_empty(tmp_path):
    assert HarnessLedger(tmp_path / "nope.jsonl").read_all() == []


def test_ledger_strict_read_raises_on_corrupt_line(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = HarnessLedger(path)
    ledger.append(_record(variant="base"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    with pytest.raises(HarnessMetricsError, match="usable record"):
        ledger.read_all()


def test_ledger_quarantine_reports_bad_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = HarnessLedger(path)
    ledger.append(_record(variant="base"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"broken": true}\n')
    bad = ledger.quarantine()
    assert len(bad) == 1
    assert bad[0][0] == 2
    # The valid prefix still reads back.
    assert len(ledger.read_all(strict=False)) == 1


def test_pair_up_skips_unpaired_arms(tmp_path):
    ledger = HarnessLedger(tmp_path / "ledger.jsonl")
    ledger.append(_record(variant="base", task_id="paired"))
    ledger.append(_record(variant="candidate", task_id="paired", tokens_miss=500))
    ledger.append(_record(variant="base", task_id="orphan"))
    pairs = ledger.pair_up()
    assert len(pairs) == 1
    assert pairs[0][0].task_id == "paired"


def test_judge_all_and_summary_report_the_split(tmp_path):
    ledger = HarnessLedger(tmp_path / "ledger.jsonl")
    # one accepted pair
    ledger.append(_record(variant="base", task_id="good", tokens_miss=1000))
    ledger.append(_record(variant="candidate", task_id="good", tokens_miss=500))
    # one refused pair (no efficiency gain)
    ledger.append(_record(variant="base", task_id="flat", tokens_miss=1000))
    ledger.append(_record(variant="candidate", task_id="flat", tokens_miss=1000))
    summary = ledger.summary()
    assert summary["pairs"] == 2
    assert summary["accepted"] == 1
    assert summary["refused"] == 1
    assert summary["tokens_base"] == 2000
    assert summary["tokens_candidate"] == 1500
    assert summary["token_gain_fraction"] == pytest.approx(0.25)


def test_mechanism_filter_isolates_experiments(tmp_path):
    ledger = HarnessLedger(tmp_path / "ledger.jsonl")
    for mechanism in ("action_fusion", "observation_pack"):
        ledger.append(_record(mechanism=mechanism, variant="base"))
        ledger.append(
            _record(mechanism=mechanism, variant="candidate", tokens_miss=500)
        )
    assert len(ledger.pair_up("action_fusion")) == 1
    assert ledger.summary("observation_pack")["pairs"] == 1


def test_ledger_written_lines_are_valid_json_objects(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = HarnessLedger(path)
    ledger.append(_record(variant="base"))
    line = path.read_text(encoding="utf-8").strip()
    assert json.loads(line)["mechanism"] == "observation_pack"
