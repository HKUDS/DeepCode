"""P2-E2 (GenAI lessons 13/14): groundedness spot-check.

Lesson 13 lists *output validation* among the four security-testing methods;
lesson 14's Honesty/groundedness metric asks "does the answer follow from the
supplied evidence?". This module provides a pure-mechanism spot-check: split a
final answer into sentences, and for each sentence that makes an evidential
claim, verify it is *supported* by the retrieved/injected evidence text.

Scoring (no LLM): a sentence is ``supported`` when a substantial fraction of
its content tokens appear in the evidence; ``unsupported`` when it claims
specific facts absent from the evidence. Optionally a caller can supply an
LLM-as-judge callable for paraphrase-tolerant judgement (``judge_fn``) — the
module stays mechanism-only by default.

Deliberately a *spot-check*: run on a sample or on critical decisions, never
on every turn (lesson 14: cost control).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
        "to", "of", "in", "on", "for", "with", "at", "by", "from", "as",
        "that", "this", "it", "its", "we", "our", "you", "your", "i", "me",
        "my", "be", "been", "being", "have", "has", "had", "do", "does",
        "did", "will", "would", "can", "could", "should", "not", "no",
        "yes", "so", "if", "then", "than", "there", "here", "which", "who",
        "when", "where", "why", "how", "all", "any", "both", "each", "few",
        "more", "most", "other", "some", "such", "only", "own", "same",
    }
)

_SENTENCE = re.compile(
    r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s+(?=[A-Z0-9])"
)
_WORD = re.compile(r"[a-z0-9']+")

_SUPPORT_THRESHOLD = 0.5  # fraction of content tokens present in evidence
_MIN_SENTENCE_TOKENS = 2  # ignore fragments like "42" or "Done."


def _content_tokens(text: str) -> set[str]:
    return {
        w
        for w in _WORD.findall(str(text).lower())
        if w not in _STOPWORDS and len(w) > 1
    }


def _split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, keeping 'src/parser.py.' intact.

    Uses a lookbehind-boundary split (period/question/exclamation followed by
    whitespace + capital) instead of a naive character class, so dotted paths
    and abbreviations do not fragment into fake sentences.
    """
    parts = re.split(_SENTENCE, str(text))
    return [p.strip() for p in parts if p.strip()]


@dataclass
class GroundednessVerdict:
    """One sentence's support verdict."""

    sentence: str
    supported: bool
    coverage: float
    reason: str = ""


@dataclass
class GroundednessReport:
    """Aggregate spot-check over an answer against its evidence."""

    answer: str
    evidence: str
    verdicts: list[GroundednessVerdict] = field(default_factory=list)

    @property
    def supported_ratio(self) -> float:
        if not self.verdicts:
            return 0.0
        return sum(1 for v in self.verdicts if v.supported) / len(self.verdicts)

    def unsupported_sentences(self) -> list[GroundednessVerdict]:
        return [v for v in self.verdicts if not v.supported]


def check_groundedness(
    answer: str,
    evidence: str,
    *,
    threshold: float = _SUPPORT_THRESHOLD,
    judge_fn: Callable[[str, str], bool] | None = None,
) -> GroundednessReport:
    """Split ``answer`` into sentences and judge each against ``evidence``.

    ``judge_fn(sentence, evidence) -> bool`` lets a caller plug an
    LLM-as-judge for paraphrase-tolerant checks; when absent the default
    token-coverage heuristic runs (pure mechanism, zero cost).
    """
    answer = str(answer or "")
    evidence = str(evidence or "")
    evidence_tokens = _content_tokens(evidence)
    report = GroundednessReport(answer=answer, evidence=evidence)

    for sentence in _split_sentences(answer):
        if judge_fn is not None:
            try:
                supported = bool(judge_fn(sentence, evidence))
            except Exception:  # noqa: BLE001 - judge failure is a soft miss
                supported = False
            report.verdicts.append(
                GroundednessVerdict(
                    sentence=sentence,
                    supported=supported,
                    coverage=1.0 if supported else 0.0,
                    reason="judge_fn" if supported else "judge_fn (failed or false)",
                )
            )
            continue
        tokens = _content_tokens(sentence)
        if len(tokens) < _MIN_SENTENCE_TOKENS:
            continue  # non-evidential fragment (e.g. a bare number)
        present = sum(1 for t in tokens if t in evidence_tokens)
        coverage = present / len(tokens)
        supported = coverage >= threshold
        report.verdicts.append(
            GroundednessVerdict(
                sentence=sentence,
                supported=supported,
                coverage=round(coverage, 3),
                reason=(
                    f"{present}/{len(tokens)} content tokens in evidence"
                    if supported
                    else (
                        f"only {present}/{len(tokens)} content tokens in "
                        "evidence; facts may be fabricated"
                    )
                ),
            )
        )
    return report


__all__ = [
    "GroundednessReport",
    "GroundednessVerdict",
    "check_groundedness",
]
