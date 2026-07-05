"""Tests for the per-model metadata catalog (context window / output / price)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
