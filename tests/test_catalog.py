"""Tests for the per-model metadata catalog (context window / output / price)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers import catalog  # noqa: E402


def test_exact_seed_hit():
    info = catalog.resolve_model_info("gpt-5.4")
    assert info.context_window == 400_000
    assert info.max_output_tokens == 128_000
    assert info.source == "seed"


def test_provider_prefix_stripped_and_lowercased():
    # OpenRouter-style "openai/GPT-5.4" resolves to the same bare seed entry.
    info = catalog.resolve_model_info("openai/GPT-5.4")
    assert info.context_window == 400_000
    assert info.source == "seed"


def test_family_fallback_for_unseen_point_release():
    # A model the seed has never listed still inherits its family's window,
    # not the crude global default — the whole point of the family cascade.
    info = catalog.resolve_model_info("gpt-5.9-turbo")
    assert info.context_window == 400_000
    assert info.source == "family:gpt-5"


def test_family_fallback_specificity_order():
    # "claude-opus-*" must match the opus rule, not the generic "claude" one.
    opus = catalog.resolve_model_info("claude-opus-4-9")
    assert opus.max_output_tokens == 32_000
    assert opus.source == "family:claude-opus"


def test_unknown_model_takes_conservative_default():
    info = catalog.resolve_model_info("some-homegrown-llm")
    assert info.context_window == 128_000
    assert info.source == "default"


def test_none_model_is_default():
    assert catalog.resolve_model_info(None).source == "default"


def test_kimi_k3_has_a_conservative_offline_one_million_token_window():
    info = catalog.resolve_model_info("moonshotai/kimi-k3")

    assert info.context_window == 1_048_576
    assert info.max_output_tokens == 128_000


def test_convenience_helpers():
    assert catalog.context_window_for("gemini-2.5-pro") == 1_048_576
    assert catalog.max_output_tokens_for("claude-sonnet-5") == 64_000


def test_snapshot_overrides_seed(tmp_path, monkeypatch):
    # A models.dev-shaped export wins over the built-in seed once merged.
    snap = tmp_path / "models.json"
    snap.write_text(
        json.dumps(
            {
                "gpt-5.4": {
                    "limit": {"context": 500_000, "output": 200_000},
                    "cost": {"input": 2.0, "output": 12.0},
                }
            }
        )
    )
    monkeypatch.setattr(catalog, "_SNAPSHOT", {})
    merged = catalog.load_catalog_snapshot(snap)
    assert merged == 1
    info = catalog.resolve_model_info("gpt-5.4")
    assert info.context_window == 500_000
    assert info.source == "snapshot"


def test_snapshot_skips_entries_without_context(tmp_path, monkeypatch):
    snap = tmp_path / "partial.json"
    snap.write_text(json.dumps({"weird-model": {"cost": {"input": 1.0}}}))
    monkeypatch.setattr(catalog, "_SNAPSHOT", {})
    assert catalog.load_catalog_snapshot(snap) == 0


# Vendor page read 2026-09-27: https://api-docs.deepseek.com/quick_start/pricing
# ``deepseek-flash`` is the current name for V4.1-Flash; ``deepseek-v4-flash`` is
# a retired alias the vendor still accepts and still bills at the Flash price.
# Both must carry the vendor row instead of falling through to the
# ``deepseek`` family rule, which also handed V4 a 128K window.
@pytest.mark.parametrize("model_id", ["deepseek-flash", "deepseek-v4-flash"])
def test_deepseek_v4_flash_ids_carry_the_vendor_row(model_id):
    info = catalog.resolve_model_info(model_id)

    assert info.source == "seed"
    assert info.context_window == 1_000_000
    assert info.max_output_tokens == 384_000


def test_deepseek_v4_tiers_are_priced_apart():
    # Regression: both V4 tiers used to fall through to the ``deepseek`` family
    # rule and take ``deepseek-v3``'s price, so pro and flash were one row in
    # the cost ledger (found by the layer-④ cost census). V4 is priced by time of
    # day; the seeded numbers are the peak rate, i.e. the upper bound.
    flash = catalog.resolve_model_info("deepseek-v4-flash")
    pro = catalog.resolve_model_info("deepseek-v4-pro")
    v3 = catalog.resolve_model_info("deepseek-v3")

    assert flash.source == "seed"
    assert pro.source == "seed"
    assert pro.input_cost_per_1m > flash.input_cost_per_1m
    assert pro.output_cost_per_1m > flash.output_cost_per_1m
    assert flash.input_cost_per_1m != v3.input_cost_per_1m


@pytest.mark.parametrize(
    "spelling",
    [
        "deepseek-v4-flash",
        "deepseek/deepseek-v4-flash",
        "deepseek-ai/DeepSeek-V4-Flash",
    ],
)
def test_observed_gateway_spellings_fold_onto_one_seed_row(spelling):
    # All three spellings appear in the gateway log for the same logical model.
    # Prefix-stripping must fold them onto a single id, or cost accounting
    # fragments by spelling instead of by model.
    info = catalog.resolve_model_info(spelling)

    assert info.id == "deepseek-v4-flash"
    assert info.source == "seed"


@pytest.mark.parametrize(
    ("model_id", "expected_source"),
    [
        ("deepseek-v4-turbo", "family:deepseek-v4"),
        ("deepseek-flash-turbo", "family:deepseek-flash"),
    ],
)
def test_unseeded_point_release_does_not_inherit_v3(model_id, expected_source):
    info = catalog.resolve_model_info(model_id)
    v3 = catalog.resolve_model_info("deepseek-v3")

    assert info.source == expected_source
    assert info.input_cost_per_1m != v3.input_cost_per_1m
