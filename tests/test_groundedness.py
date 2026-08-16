"""P2-E2: groundedness spot-check (GenAI lessons 13/14)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.groundedness import check_groundedness

_EVIDENCE = (
    "The parser module lives in src/parser.py. It uses the tokenize library. "
    "Tests run with pytest and must stay green before merging."
)


def test_supported_answer_high_ratio():
    answer = (
        "The parser module is in src/parser.py. "
        "It uses the tokenize library."
    )
    report = check_groundedness(answer, _EVIDENCE)
    assert report.supported_ratio == 1.0
    assert report.unsupported_sentences() == []


def test_fabricated_claim_flagged():
    answer = (
        "The parser module is in src/parser.py. "
        "The quantum compiler runs on a GPU cluster."
    )
    report = check_groundedness(answer, _EVIDENCE)
    verdicts = report.verdicts
    assert verdicts[0].supported is True
    assert verdicts[1].supported is False
    assert "fabricated" in verdicts[1].reason


def test_empty_answer_and_evidence():
    assert check_groundedness("", _EVIDENCE).verdicts == []
    assert check_groundedness("Some sentence.", "").supported_ratio == 0.0


def test_judge_fn_used_when_provided():
    calls = []

    def judge(sentence, evidence):
        calls.append(sentence)
        return "compiler" not in sentence

    answer = "The parser module is in src/parser.py. The quantum compiler runs."
    report = check_groundedness(answer, _EVIDENCE, judge_fn=judge)
    assert len(calls) == 2
    assert report.verdicts[0].supported is True
    assert report.verdicts[1].supported is False


def test_non_evidential_fragment_skipped():
    # A bare number/heading has no content tokens → no verdict, not a failure.
    report = check_groundedness("42", _EVIDENCE)
    assert report.verdicts == []
