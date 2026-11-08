"""Tests for the post-compaction fog observation and its opt-in JSONL ledger."""

from __future__ import annotations

import json

import pytest

from core.agent_runtime.fog_ledger import (
    FogLedger,
    FogLedgerError,
    FogObservation,
    measure_post_compaction_fog,
)


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
    with pytest.raises(FogLedgerError):
        measure_post_compaction_fog([], window_steps=bad)


def test_fog_rejects_non_mapping_messages():
    with pytest.raises(FogLedgerError):
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
    with pytest.raises(FogLedgerError):
        measure_post_compaction_fog(messages)


def test_fog_observation_rejects_impossible_counts():
    with pytest.raises(FogLedgerError):
        FogObservation(
            window_steps=28,
            compactions=0,
            post_steps=1,
            post_error_steps=2,
            baseline_steps=0,
            baseline_error_steps=0,
        )
    with pytest.raises(FogLedgerError):
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
    with pytest.raises(FogLedgerError):
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
    # Separation invariant: the row carries the six counts and nothing else,
    # so a paired harness record can never be mistaken for a fog observation.


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
