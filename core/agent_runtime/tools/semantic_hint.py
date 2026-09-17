"""P2-A7 (GenAI lesson 17): tool-name miss semantic candidates.

Taskweaver stores plugins as embeddings and lets the LLM *semantically
search* for the right plugin when the tool count grows. DeepCode routes tools
by exact name; when the model hallucinates or misremembers a name, the
registry returns "not found". This module adds the cheap first step: given the
missed name and the available tool names, suggest the closest candidates by
token-overlap similarity (no LLM, no embeddings — pure static scoring).

Design guard (lesson 13): semantic discovery is only a *hint* fed back to the
model as an error message; execution still requires the exact registered name
plus the permission engine. It never widens the callable surface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(name: str) -> set[str]:
    return set(_WORD.findall(str(name).lower()))


def _name_similarity(a: str, b: str) -> float:
    """Combined token-overlap + sequence similarity in [0, 1]."""
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        overlap = len(ta & tb) / max(len(ta | tb), 1)
    else:
        overlap = 0.0
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return max(overlap, seq * 0.8)


def suggest_tools(
    missed_name: str,
    available: Iterable[str],
    *,
    top_k: int = 3,
    min_similarity: float = 0.35,
) -> list[str]:
    """Candidates for a missed tool name, best first (empty when none close).

    ``min_similarity`` guards against suggesting unrelated tools; below it the
    caller should just report "not found" without noise (lesson 17: don't
    widen the surface with guesses).
    """
    scored = [
        (candidate, _name_similarity(missed_name, candidate))
        for candidate in available
        if candidate != missed_name
    ]
    scored = [(name, score) for name, score in scored if score >= min_similarity]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [name for name, _score in scored[:top_k]]


def build_miss_message(
    missed_name: str,
    available: Iterable[str],
    *,
    top_k: int = 3,
    min_similarity: float = 0.35,
) -> str:
    """Error-message helper: "not found" + semantic candidates (if any)."""
    candidates = suggest_tools(
        missed_name, available, top_k=top_k, min_similarity=min_similarity
    )
    if not candidates:
        return f"Tool '{missed_name}' not found."
    return (
        f"Tool '{missed_name}' not found. Did you mean one of: "
        + ", ".join(candidates)
        + "?"
    )


__all__ = ["build_miss_message", "suggest_tools"]
