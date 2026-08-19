"""P1-6: retrieval failure-mode mitigations (GenAI lesson 15).

Pins the three explicit mitigations: similarity threshold + no-memory
fallback (mode 1), hybrid recall contract on scored entries (mode 2), and the
"retrieved but not used" self-check (mode 3 — the course notebook's real bug).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.memory_retrieval import (
    DEFAULT_SIMILARITY_THRESHOLD,
    accepted_entries,
    assert_all_injected,
    compose_memory_injection,
    no_memory_statement,
)

# ---- mode 1: threshold + explicit no-memory fallback -------------------------


def test_below_threshold_entries_rejected():
    entries = [
        {"content": "weak match", "similarity": 0.2},
        {"content": "strong match", "similarity": 0.9},
        {"content": "borderline", "similarity": DEFAULT_SIMILARITY_THRESHOLD},
    ]
    accepted = accepted_entries(entries)
    assert [e["content"] for e in accepted] == ["strong match", "borderline"]


def test_empty_entries_give_nothing_and_fallback():
    assert accepted_entries([]) == []
    assert accepted_entries([{"content": "x", "similarity": 0.1}]) == []
    statement = no_memory_statement()
    assert "No relevant past-session memory" in statement
    assert "do not invent facts" in statement


def test_score_alias_accepted():
    entries = [{"content": "generic shape", "score": 0.8}]
    assert [e["content"] for e in accepted_entries(entries)] == ["generic shape"]


def test_malformed_entries_skipped():
    entries = [
        {"content": "no similarity"},
        {"content": "", "similarity": 0.9},
        "not-a-dict",
        {"content": "bad score", "similarity": "nan"},
    ]
    assert accepted_entries(entries) == []


def test_sorted_by_similarity_desc():
    entries = [
        {"content": "b", "similarity": 0.6},
        {"content": "a", "similarity": 0.9},
    ]
    assert [e["content"] for e in accepted_entries(entries)] == ["a", "b"]


# ---- mode 2: hybrid recall contract ------------------------------------------


def test_keyword_and_semantic_shapes_merge():
    # Cerebellum returns keyword_hits (no score) + semantic_hits (scored).
    # The injection layer accepts the scored semantic side and drops
    # unscored keyword hits unless they carry a similarity — the store owns
    # hybrid fusion; DeepCode enforces the threshold on scored entries.
    entries = [
        {"content": "kw hit (unscored)"},
        {"content": "sem hit", "similarity": 0.88},
    ]
    accepted = accepted_entries(entries)
    assert len(accepted) == 1
    assert accepted[0]["content"] == "sem hit"


# ---- mode 3: retrieved-but-not-used self-check -------------------------------


def test_assert_all_injected_passes_when_chain_intact():
    entries = [{"content": "fact one", "similarity": 0.9}]
    injected = compose_memory_injection(entries)
    assert assert_all_injected(entries, injected) == []


def test_assert_all_injected_detects_dropped_entry():
    # The course notebook bug: retrieved into history, only history[-1] used.
    entries = [
        {"content": "fact one", "similarity": 0.9},
        {"content": "fact two", "similarity": 0.85},
    ]
    injected = compose_memory_injection([entries[0]])  # only the first made it
    missing = assert_all_injected(entries, injected)
    assert any("fact two" in m for m in missing)


def test_assert_all_injected_ignores_below_threshold():
    entries = [
        {"content": "weak", "similarity": 0.1},
        {"content": "strong", "similarity": 0.9},
    ]
    injected = compose_memory_injection(entries)  # weak filtered out
    assert assert_all_injected(entries, injected) == []


# ---- injection rendering -----------------------------------------------------


def test_compose_memory_injection_is_data_bounded_and_numbered():
    entries = [{"content": "rule one", "similarity": 0.9, "source_key": "s1"}]
    block = compose_memory_injection(entries)
    assert "[1]" in block
    assert "(from s1)" in block
    assert "rule one" in block
    assert "untrusted reference data" in block  # P1-3 restrict clause rides along


def test_compose_empty_when_nothing_clears_threshold():
    assert compose_memory_injection([{"content": "x", "similarity": 0.2}]) == ""
