"""Tests for the paired harness-measurement ledger (SoL-Pi two-gate port)."""

from __future__ import annotations

import json

import pytest

from core.agent_runtime.harness_metrics import (
    CapabilityOutcome,
    EfficiencySample,
    FogLedger,
    FogObservation,
    HarnessLedger,
    HarnessMetricsError,
    HarnessRecord,
    ModelIdentity,
    judge_pair,
    measure_post_compaction_fog,
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


# --------------------------------------------------------------------------
# Post-compaction fog
# --------------------------------------------------------------------------


def _step(*, error: bool = False, content: object | None = None) -> list[dict]:
    """One assistant turn plus a single tool result (the unit counted as a step)."""
    return [
        {"role": "assistant", "content": "working on it"},
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "bash",
            "content": content
            if content is not None
            else ("Error: command failed" if error else "ok"),
        },
    ]


def _checkpoint(*, retain: int = 0) -> dict:
    return {
        "role": "user",
        "content": "This is your own earlier conversation ... Summary:",
        "compaction": {"reset": True, "retain": retain},
    }


def test_fog_counts_error_steps_inside_window_against_baseline():
    messages = [
        {"role": "user", "content": "task"},
        *_step(error=True),  # pre-compaction step: baseline
        _checkpoint(),
        *_step(error=True),  # post-compaction error step
        *_step(),  # post-compaction clean step
    ]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.compactions == 1
    assert fog.post_steps == 2
    assert fog.post_error_steps == 1
    assert fog.baseline_steps == 1
    assert fog.baseline_error_steps == 1
    assert fog.post_error_rate == pytest.approx(0.5)
    assert fog.baseline_error_rate == pytest.approx(1.0)


def test_fog_window_is_capped_at_window_steps():
    messages = [
        _checkpoint(),
        *_step(),
        *_step(),
        *_step(error=True),  # third step: outside a 2-step window
        *_step(error=True),  # fourth step: outside too
    ]
    fog = measure_post_compaction_fog(messages, window_steps=2)
    assert fog.post_steps == 2
    assert fog.post_error_steps == 0
    assert fog.baseline_steps == 2
    assert fog.baseline_error_steps == 2


def test_fog_skips_retained_tail_before_window_opens():
    """The retained tail is pre-compaction work; it must not consume the window."""
    messages = [
        _checkpoint(retain=2),
        {"role": "assistant", "content": "old turn kept verbatim"},
        {
            "role": "tool",
            "tool_call_id": "call-old",
            "name": "bash",
            "content": "Error: stale pre-compaction failure",
        },
        *_step(error=True),
        *_step(),
    ]
    fog = measure_post_compaction_fog(messages)
    assert fog.post_steps == 2
    assert fog.post_error_steps == 1
    assert fog.baseline_steps == 1
    assert fog.baseline_error_steps == 1


def test_fog_detects_checkpoint_by_compaction_key_only():
    summary_like = {
        "role": "user",
        "content": "This is your own earlier conversation ... Summary: old context",
    }
    keyed = {
        "role": "user",
        "content": "plain text",
        "compaction": {"reset": True, "retain": 0},
    }
    messages = [summary_like, *_step(error=True), keyed, *_step()]
    fog = measure_post_compaction_fog(messages)
    assert fog.compactions == 1
    assert fog.post_steps == 1
    assert fog.post_error_steps == 0
    assert fog.baseline_steps == 1
    assert fog.baseline_error_steps == 1


def test_fog_error_signal_is_the_runner_error_prefix():
    """Only the runner's uniform "Error: " rendering counts; lookalikes do not."""
    messages = [
        _checkpoint(),
        *_step(content="Error: real failure"),
        *_step(content="error: lowercase rendering"),
        *_step(content="Warning: Error: embedded but not a prefix"),
        *_step(content="ok"),
    ]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.post_steps == 4
    assert fog.post_error_steps == 1


def test_fog_ignores_non_string_tool_content():
    """Structured tool results and empty-output placeholders are never errors."""
    messages = [
        _checkpoint(),
        *_step(content={"type": "text", "text": "Error: inside a dict"}),
        *_step(content=[{"type": "text", "text": "Error: block"}]),
        *_step(content="(bash completed with no output)"),
    ]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.post_steps == 3
    assert fog.post_error_steps == 0


def test_fog_orphan_tool_results_are_not_steps():
    messages = [_checkpoint(), _step()[1], *_step()]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.post_steps == 1
    assert fog.post_error_steps == 0


def test_fog_rates_are_none_without_samples():
    fog = measure_post_compaction_fog([])
    assert fog.compactions == 0
    assert fog.post_steps == 0
    assert fog.baseline_steps == 0
    assert fog.post_error_rate is None
    assert fog.baseline_error_rate is None
    assert fog.fog_ratio is None


def test_fog_ratio_compares_post_against_baseline():
    messages = [
        *_step(),
        *_step(),
        *_step(),
        *_step(error=True),  # baseline: 1/4 = 0.25
        _checkpoint(),
        *_step(error=True),
        *_step(error=True),  # post: 2/2 = 1.0
    ]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.fog_ratio == pytest.approx(4.0)


def test_fog_ratio_is_none_when_baseline_rate_is_zero():
    messages = [*_step(), _checkpoint(), *_step(error=True)]
    fog = measure_post_compaction_fog(messages, window_steps=28)
    assert fog.baseline_error_rate == pytest.approx(0.0)
    assert fog.fog_ratio is None  # undefined, not infinite


def test_fog_windows_union_across_checkpoints_without_double_counting():
    messages = [
        _checkpoint(),
        *_step(),
        *_step(),
        _checkpoint(),
        *_step(error=True),
        *_step(),
    ]
    fog = measure_post_compaction_fog(messages, window_steps=4)
    assert fog.compactions == 2
    assert fog.post_steps == 4  # the second window overlaps the first's tail
    assert fog.post_error_steps == 1
    assert fog.baseline_steps == 0


def test_fog_does_not_mutate_the_message_list():
    messages = [
        {"role": "user", "content": "task"},
        *_step(error=True),
        _checkpoint(retain=1),
        *_step(),
    ]
    before = json.loads(json.dumps(messages))
    measure_post_compaction_fog(messages)
    assert messages == before


@pytest.mark.parametrize("bad", [0, -1, True, "28"])
def test_fog_rejects_bad_window_steps(bad):
    with pytest.raises(HarnessMetricsError):
        measure_post_compaction_fog([], window_steps=bad)


def test_fog_rejects_non_mapping_messages():
    with pytest.raises(HarnessMetricsError):
        measure_post_compaction_fog(["not a mapping"])


@pytest.mark.parametrize("retain", [-1, "2", True, 1.5])
def test_fog_rejects_malformed_retain_count(retain):
    messages = [
        {
            "role": "user",
            "content": "s",
            "compaction": {"reset": True, "retain": retain},
        }
    ]
    with pytest.raises(HarnessMetricsError):
        measure_post_compaction_fog(messages)


def test_fog_observation_rejects_impossible_counts():
    with pytest.raises(HarnessMetricsError):
        FogObservation(
            window_steps=28,
            compactions=0,
            post_steps=1,
            post_error_steps=2,
            baseline_steps=0,
            baseline_error_steps=0,
        )
    with pytest.raises(HarnessMetricsError):
        FogObservation(
            window_steps=28,
            compactions=0,
            post_steps=1,
            post_error_steps=0,
            baseline_steps=1,
            baseline_error_steps=2,
        )


def test_fog_observation_rejects_bool_counters():
    """A bool is an int in Python's type system but never a valid count."""
    with pytest.raises(HarnessMetricsError):
        FogObservation(
            window_steps=28,
            compactions=True,
            post_steps=0,
            post_error_steps=0,
            baseline_steps=0,
            baseline_error_steps=0,
        )


def test_fog_observation_to_dict_round_trips_raw_counts():
    """Serialized rows carry counts only; readers recompute the rates."""
    obs = FogObservation(
        window_steps=28,
        compactions=2,
        post_steps=6,
        post_error_steps=3,
        baseline_steps=14,
        baseline_error_steps=4,
    )
    payload = obs.to_dict()
    assert set(payload) == {
        "window_steps",
        "compactions",
        "post_steps",
        "post_error_steps",
        "baseline_steps",
        "baseline_error_steps",
    }
    assert FogObservation(**payload) == obs
    # Separation invariant: fog rows never parse as harness records.
    with pytest.raises(HarnessMetricsError):
        HarnessRecord.from_dict(payload)


def test_fog_ledger_appends_one_json_line_per_observation(tmp_path):
    """FogLedger creates its own nested path and writes replayable lines."""
    path = tmp_path / "fog" / "fog.jsonl"
    ledger = FogLedger(path)
    obs = FogObservation(
        window_steps=28,
        compactions=1,
        post_steps=4,
        post_error_steps=2,
        baseline_steps=10,
        baseline_error_steps=3,
    )
    ledger.append(obs)
    ledger.append(obs)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [obs.to_dict(), obs.to_dict()]


def test_fog_ledger_records_first_compaction_as_baseline_only(tmp_path):
    """First compaction has no window yet: the row stays baseline-only."""
    messages = [{"role": "user", "content": "task"}] + _step(error=True) + _step()
    obs = measure_post_compaction_fog(messages)
    assert obs.compactions == 0
    assert obs.post_steps == 0
    assert obs.baseline_steps == 2
    assert obs.baseline_error_steps == 1
    ledger = FogLedger(tmp_path / "fog.jsonl")
    ledger.append(obs)
    row = json.loads((tmp_path / "fog.jsonl").read_text(encoding="utf-8"))
    assert row["compactions"] == 0
    assert row["post_steps"] == 0
