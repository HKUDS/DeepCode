"""P1-7 (GenAI lesson 15): heterogeneous held-out retrieval evaluation.

Lesson 15's weak-evaluation traps: (a) eval sets built from the very documents
being indexed inflate scores (same-source), and (b) exact-string scoring has
zero tolerance for paraphrase. Cerebellum's built-in ``benchmark_run`` suffers
both — its QA set is built from the indexed entries themselves and hits are
scored by exact ``source_key`` equality. This module fixes both on the
DeepCode side:

* **Held-out QA set** — :func:`split_held_out_qa` pulls evaluation questions
  from sources *excluded* from the indexed store, so recall measures
  generalization, not self-consistency.
* **Semantic scoring** — :func:`evaluate_retrieval` scores a hit when the
  *content* embedding is similar to the gold answer (default threshold),
  never by string equality. Without an embedder it degrades to exact-substring
  matching and reports ``weak=True`` so nobody mistakes it for a semantic
  score.

Standalone module (no dependency on ``cerebellum_optimizer``) so it can land
independently of the cerebellum skill-evolution loop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Cerebellum evolution module (its __init__ inserts its own dir into sys.path).
_CEREBELLUM_EVOLUTION = (
    Path(__file__).resolve().parents[2]
    / ".dsh"
    / "skills"
    / "deepcode-cerebellum"
    / "cerebellum_evolution.py"
)


def _import_cerebellum() -> Any:
    """Import cerebellum_evolution, tolerating a missing cerebellum."""
    module = str(_CEREBELLUM_EVOLUTION)
    if not Path(module).is_file():
        raise FileNotFoundError(f"cerebellum not found at {module}")
    spec = importlib.util.spec_from_file_location("cerebellum_evolution", module)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _cosine_similarity(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def split_held_out_qa(
    entries: list[dict[str, Any]],
    hold_out_sources: set[str],
    *,
    query_chars: int = 60,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Split scored/indexable entries into a held-out QA set + indexed sources.

    ``entries`` are ``{"content", "source", ...}`` rows. Every entry whose
    ``source`` is in ``hold_out_sources`` becomes an evaluation question
    (query = content prefix, gold = full content); those sources must NOT be
    present in the store the evaluator searches, or the eval is contaminated
    (lesson 15: same-source eval inflates scores). Returns
    ``(qa_set, indexed_sources)`` where ``indexed_sources`` = the sources that
    stay in the index.
    """
    qa: list[dict[str, Any]] = []
    indexed: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content", "")).strip()
        source = str(entry.get("source", "") or "")
        if not content:
            continue
        if source in hold_out_sources:
            query = content[:query_chars] + ("…" if len(content) > query_chars else "")
            qa.append({"query": query, "gold": content, "source": source})
        else:
            indexed.add(source)
    return qa, indexed


def evaluate_retrieval(
    qa_set: list[dict[str, Any]],
    *,
    search_fn: Any,
    embed_fn: Any | None = None,
    top_k: int = 5,
    similarity_threshold: float = 0.45,
) -> dict[str, Any]:
    """Held-out retrieval evaluation with semantic scoring (P1-7).

    Parameters
    ----------
    qa_set:
        ``[{"query", "gold", ...}]`` — queries heterogeneously sourced from
        documents NOT in the searched index.
    search_fn:
        ``(query, limit) -> [{"content", ...}]`` — the retrieval channel
        (e.g. cerebellum ``memory_search`` semantic_hits adapter).
    embed_fn:
        ``(text) -> list[float] | None`` — semantic embedder. When None,
        scoring degrades to exact-substring matching and the result carries
        ``weak=True`` (an explicit warning, not a silent downgrade).
    similarity_threshold:
        Minimum content-embedding cosine for a hit to count as the gold.

    Returns metrics ``{queries, recall@1, recall@k, mrr, weak, per_query}`` —
    same shape family as cerebellum's ``benchmark_run`` so callers can compare.
    """
    results: dict[str, Any] = {
        "queries": len(qa_set),
        "top_k": top_k,
        "recall@1": 0.0,
        f"recall@{top_k}": 0.0,
        "mrr": 0.0,
        "weak": embed_fn is None,
        "per_query": [],
    }
    if not qa_set:
        return results

    gold_vectors: list[list[float] | None] = []
    if embed_fn is not None:
        for item in qa_set:
            try:
                gold_vectors.append(embed_fn(str(item.get("gold", ""))))
            except Exception:  # noqa: BLE001 - a bad embed must not kill the eval
                gold_vectors.append(None)

    hits = 0
    hits_at_1 = 0
    mrr_sum = 0.0
    for index, item in enumerate(qa_set):
        query = str(item.get("query", ""))
        gold = str(item.get("gold", ""))
        try:
            retrieved = search_fn(query, top_k) or []
        except Exception:  # noqa: BLE001 - retrieval failure counts as a miss
            retrieved = []
        rank = 0
        for position, hit in enumerate(retrieved, start=1):
            content = str((hit or {}).get("content", "")).strip()
            if not content:
                continue
            if embed_fn is not None:
                try:
                    sim = _cosine_similarity(gold_vectors[index], embed_fn(content))
                except Exception:  # noqa: BLE001
                    sim = 0.0
                if sim >= similarity_threshold:
                    rank = position
                    break
            elif gold and gold in content:
                rank = position
                break
        if rank:
            hits += 1
            if rank == 1:
                hits_at_1 += 1
            mrr_sum += 1.0 / rank
        results["per_query"].append({"query": query, "rank": rank})

    n = len(qa_set)
    results["recall@1"] = round(hits_at_1 / n, 3)
    results[f"recall@{top_k}"] = round(hits / n, 3)
    results["mrr"] = round(mrr_sum / n, 3)
    return results


def cerebellum_search_adapter(
    db_path: str | Path | None = None,
) -> Any:
    """Adapter: cerebellum ``memory_search`` semantic_hits → search_fn contract.

    Returns ``(query, limit) -> [{"content", "similarity", ...}]`` (the raw
    semantic hits), or an always-empty callable when cerebellum is missing —
    evaluation must never crash on a missing component.
    """

    def _search(query: str, limit: int) -> list[dict[str, Any]]:
        try:
            mod = _import_cerebellum()
            mem = mod.CerebellumMemory(db_path or mod.DEFAULT_DB)
            result = mem.search(query, limit=limit)
            return result.get("semantic_hits", []) or []
        except Exception:  # noqa: BLE001 - evaluation must never crash
            logger.debug("cerebellum search adapter failed", exc_info=True)
            return []

    return _search


def cerebellum_embed_adapter(
    db_path: str | Path | None = None,
) -> Any | None:
    """Adapter: cerebellum ``ollama_embed`` → embed_fn contract, or None.

    ``None`` means no embedder is available (cerebellum missing/unimportable);
    callers should then treat the evaluation as ``weak=True`` rather than
    fabricating a semantic score. A returned callable that yields None per
    call means the embedder is present but failed that call.
    """
    try:
        _import_cerebellum()
    except Exception:  # noqa: BLE001 - missing cerebellum is a soft condition
        return None

    def _embed(text: str) -> list[float] | None:
        try:
            mod = _import_cerebellum()
            vectors = mod.ollama_embed([text])
            return vectors[0] if vectors else None
        except Exception:  # noqa: BLE001
            return None

    return _embed


__all__ = [
    "cerebellum_embed_adapter",
    "cerebellum_search_adapter",
    "evaluate_retrieval",
    "split_held_out_qa",
]
