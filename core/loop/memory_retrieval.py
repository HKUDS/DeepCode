"""P1-6 (GenAI lesson 15): explicit mitigation for retrieval failure modes.

Lesson 15 names three failure modes an agent memory loop must handle
explicitly instead of best-effort guessing:

1. **Retrieved nothing** — no similar entry in the store. Mitigation: a
   similarity threshold plus an explicit "no memory" fallback (never
   best-effort answering from vague similarity).
2. **Retrieved the wrong thing** — pure semantic recall misses identifiers /
   API names. Mitigation: hybrid keyword + vector recall (cerebellum's
   ``memory_search`` already does this); the DeepCode side enforces the
   threshold on whatever the store returned.
3. **Retrieved but not used** — the course's own notebook retrieved chunks
   into ``history`` yet only injected ``history[-1]``: retrieved data never
   reached the prompt. Mitigation: :func:`assert_all_injected` — a self-check
   that every accepted entry actually appears in the injected text.

Pure mechanism, no LLM. Works with any store that returns scored entries
``{"content", "similarity", ...}`` (cerebellum ``semantic_hits`` shape).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.loop.injection_regression import render_data_block

# Below this similarity the entry is not "relevant enough" to inject — the
# caller should fall back to the explicit no-memory statement. Cerebellum
# already hard-filters at 0.45 internally; this is the DeepCode-side
# contract applied to whatever the store returned (default aligns).
DEFAULT_SIMILARITY_THRESHOLD = 0.45

_NO_MEMORY_STATEMENT = (
    "No relevant past-session memory was found for this topic. Proceed from "
    "first principles; do not invent facts attributed to past sessions."
)


def accepted_entries(
    entries: Iterable[dict[str, Any]],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Entries whose similarity is at/above ``threshold``, sorted by score.

    Accepts both cerebellum ``semantic_hits`` rows (``similarity`` key) and
    generic ``{"content", "score"}`` shapes (``score`` aliases similarity).
    """
    accepted: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        similarity = entry.get("similarity")
        if similarity is None:
            similarity = entry.get("score")
        try:
            value = float(similarity)
        except (TypeError, ValueError):
            continue
        if value >= threshold and str(entry.get("content", "")).strip():
            accepted.append(entry)
    return sorted(accepted, key=lambda e: float(e.get("similarity") or 0), reverse=True)


def compose_memory_injection(
    entries: Iterable[dict[str, Any]],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> str:
    """Render accepted entries as a numbered, data-bounded injection block.

    Each entry becomes one ``<untrusted-data>`` block carrying the P1-3
    restrict clause (reference only, never instructions) plus its source
    metadata (``source_key`` / ``source`` when present) for traceability.
    Empty when nothing clears the threshold — the caller then uses
    :func:`no_memory_statement` instead of injecting weak matches.
    """
    accepted = accepted_entries(entries, threshold=threshold)
    blocks: list[str] = []
    for index, entry in enumerate(accepted, start=1):
        content = str(entry.get("content", "")).strip()
        if not content:
            continue
        source = entry.get("source_key") or entry.get("source") or "memory"
        header = f"[{index}] (from {source})"
        blocks.append(f"{header}\n{render_data_block(content)}")
    return "\n\n".join(blocks)


def no_memory_statement() -> str:
    """The explicit fallback when retrieval cleared nothing (failure mode 1)."""
    return _NO_MEMORY_STATEMENT


def assert_all_injected(entries: Iterable[dict[str, Any]], injected: str) -> list[str]:
    """Failure-mode-3 self-check: every *accepted* entry must appear verbatim
    in the injected text.

    Returns the list of accepted entries whose content is missing from
    ``injected`` (empty = the injection chain is intact). A non-empty result
    means the retrieval layer found data the prompt layer dropped — the exact
    "retrieved but not used" bug from the course notebook.
    """
    missing: list[str] = []
    for entry in accepted_entries(entries):
        content = str(entry.get("content", "")).strip()
        if content and content not in injected:
            missing.append(content[:80])
    return missing


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "accepted_entries",
    "assert_all_injected",
    "compose_memory_injection",
    "no_memory_statement",
]
