"""P1-7: heterogeneous held-out retrieval evaluation (GenAI lesson 15).

Lesson 15's weak-evaluation traps: (a) eval sets built from the very documents
being indexed inflate scores (same-source), and (b) exact-string scoring has
zero tolerance for paraphrase. These tests pin the two fixes — held-out QA
sources and semantic (embedding-cosine) hit scoring — on top of cerebellum's
existing MRR benchmark.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import retrieval_evaluation as re

# ---- held-out QA split ------------------------------------------------------


def test_split_held_out_isolates_sources():
    entries = [
        {"content": "alpha fact one", "source": "alpha"},
        {"content": "alpha fact two", "source": "alpha"},
        {"content": "beta fact one", "source": "beta"},
    ]
    qa, indexed = re.split_held_out_qa(entries, {"alpha"})
    assert len(qa) == 2
    assert all(q["source"] == "alpha" for q in qa)
    assert indexed == {"beta"}


def test_split_keeps_query_prefix_and_gold():
    entries = [{"content": "A very long durable fact worth remembering", "source": "x"}]
    qa, indexed = re.split_held_out_qa(entries, {"x"})
    assert indexed == set()
    assert len(qa) == 1
    assert qa[0]["gold"] == "A very long durable fact worth remembering"
    assert qa[0]["query"].startswith("A very long durable fact")
    assert len(qa[0]["query"]) <= 63  # 60 chars + ellipsis marker


def test_split_skips_blank_and_malformed():
    entries = [
        {"content": "", "source": "x"},
        {"content": "   ", "source": "x"},
        "not-a-dict",
        {"content": "valid", "source": "y"},
    ]
    qa, _indexed = re.split_held_out_qa(entries, {"x", "y"})
    assert len(qa) == 1
    assert qa[0]["gold"] == "valid"


def test_no_hold_out_keeps_all_indexed():
    entries = [{"content": "fact", "source": "a"}]
    qa, indexed = re.split_held_out_qa(entries, set())
    assert qa == []
    assert indexed == {"a"}


# ---- semantic scoring --------------------------------------------------------


def _fake_embed(text: str) -> list[float] | None:
    """Deterministic toy embedder: bag-of-tokens → vector, so cosine reflects
    token overlap (a cheap stand-in for paraphrase tolerance)."""

    tokens = {w for w in str(text).lower().split() if w.isalnum()}
    vec = [1.0 if t in tokens else 0.0 for t in ("alpha", "beta", "fact", "api")]
    return vec


def _search_returning(contents: list[str]):
    def _search(query: str, limit: int) -> list[dict]:
        return [{"content": c} for c in contents[:limit]]

    return _search


def test_semantic_hit_counts_paraphrase():
    # Gold and hit differ in wording but share tokens → cosine ≥ threshold.
    qa = [{"query": "tell me about alpha facts", "gold": "alpha fact details"}]
    search = _search_returning(["the alpha facts explained", "unrelated doc"])
    metrics = re.evaluate_retrieval(
        qa, search_fn=search, embed_fn=_fake_embed, top_k=5
    )
    assert metrics["recall@1"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["weak"] is False
    assert metrics["per_query"][0]["rank"] == 1


def test_semantic_hit_at_second_position_ranked():
    qa = [{"query": "tell me about alpha facts", "gold": "alpha fact details"}]
    search = _search_returning(["unrelated doc", "the alpha facts explained"])
    metrics = re.evaluate_retrieval(
        qa, search_fn=search, embed_fn=_fake_embed, top_k=5
    )
    assert metrics["recall@1"] == 0.0
    assert metrics[f"recall@{metrics['top_k']}"] == 1.0
    assert metrics["per_query"][0]["rank"] == 2


def test_semantic_scoring_rejects_unrelated():
    qa = [{"query": "alpha question", "gold": "alpha gold answer"}]
    search = _search_returning(["completely unrelated text"])
    metrics = re.evaluate_retrieval(qa, search_fn=search, embed_fn=_fake_embed)
    assert metrics["recall@1"] == 0.0
    assert metrics["mrr"] == 0.0
    assert metrics["per_query"][0]["rank"] == 0


def test_weak_mode_without_embedder_is_explicit():
    qa = [{"query": "q", "gold": "exact phrase"}]
    search = _search_returning(["exact phrase", "other"])
    metrics = re.evaluate_retrieval(qa, search_fn=search, embed_fn=None)
    assert metrics["weak"] is True  # explicitly flagged, not silent
    assert metrics["recall@1"] == 1.0  # exact-substring still counts
    # Exact match at position 2 → rank 2.
    search2 = _search_returning(["other", "exact phrase"])
    metrics2 = re.evaluate_retrieval(qa, search_fn=search2, embed_fn=None)
    assert metrics2["per_query"][0]["rank"] == 2


def test_empty_qa_set_returns_zeros():
    metrics = re.evaluate_retrieval([], search_fn=_search_returning([]))
    assert metrics["queries"] == 0
    assert metrics["recall@1"] == 0.0 and metrics["mrr"] == 0.0


def test_search_failure_counts_as_miss():
    def _boom(query, limit):
        raise RuntimeError("store down")

    qa = [{"query": "q", "gold": "g"}]
    metrics = re.evaluate_retrieval(qa, search_fn=_boom, embed_fn=_fake_embed)
    assert metrics["recall@1"] == 0.0
    assert metrics["per_query"][0]["rank"] == 0


# ---- adapters degrade gracefully ---------------------------------------------


def test_search_adapter_missing_cerebellum_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(re, "_CEREBELLUM_EVOLUTION", tmp_path / "nope.py")
    search = re.cerebellum_search_adapter()
    assert search("anything", 5) == []


def test_embed_adapter_missing_cerebellum_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(re, "_CEREBELLUM_EVOLUTION", tmp_path / "nope.py")
    assert re.cerebellum_embed_adapter() is None
