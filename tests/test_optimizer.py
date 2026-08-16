"""Tests for P0-3 accept/rollback optimization loop (PenguinHarness)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.optimizer import (
    ArtifactOptimizer,
    OptimizerCandidate,
)


def test_accepts_when_strictly_higher():
    applied = []

    def evaluator(candidate):
        return {"c1": 90.0, "c2": 95.0, "c3": 85.0}.get(candidate.description)

    opt = ArtifactOptimizer(
        reference="base",
        reference_score=88.0,
        evaluator=evaluator,
        apply_candidate=applied.append,
    )
    cand = OptimizerCandidate("c1")
    result = opt.run_round(cand)
    assert result.accepted and result.improved
    assert result.candidate_score == 90.0
    assert applied == [cand]
    assert opt.reference_score == 90.0  # new Reference


def test_rejects_when_not_strictly_higher():
    rollbacks = []

    def evaluator(candidate):
        return {"c1": 87.0}.get(candidate.description)

    opt = ArtifactOptimizer(
        reference="base",
        reference_score=88.0,
        evaluator=evaluator,
        rollback=rollbacks.append,
    )
    result = opt.run_round(OptimizerCandidate("c1"))
    assert not result.accepted
    assert rollbacks == ["base"]  # previous Reference restored
    assert opt.reference_score == 88.0  # unchanged


def test_rejects_equal_score():
    def evaluator(candidate):
        return 88.0

    opt = ArtifactOptimizer(reference="base", reference_score=88.0, evaluator=evaluator)
    result = opt.run_round(OptimizerCandidate("c1"))
    assert not result.accepted  # strictly higher required
    assert "not strictly higher" in result.reason


def test_rejects_invalid_evaluation():
    def evaluator(candidate):
        return None  # incomplete evaluation

    opt = ArtifactOptimizer(reference="base", reference_score=88.0, evaluator=evaluator)
    result = opt.run_round(OptimizerCandidate("c1"))
    assert not result.accepted
    assert "invalid" in result.reason


def test_min_delta_gate():
    def evaluator(candidate):
        return 88.5  # +0.5 improvement

    # min_delta=1.0 requires at least +1.0 improvement to accept.
    opt = ArtifactOptimizer(reference="base", reference_score=88.0, evaluator=evaluator)
    result = opt.run_round(OptimizerCandidate("c1"), min_delta=1.0)
    assert not result.accepted


def test_run_loop_stops_at_target():
    def evaluator(candidate):
        return {"c1": 90.0, "c2": 95.0, "c3": 99.0}.get(candidate.description)

    opt = ArtifactOptimizer(reference="base", reference_score=80.0, evaluator=evaluator)
    results = opt.run_loop(
        [
            OptimizerCandidate("c1"),
            OptimizerCandidate("c2"),
            OptimizerCandidate("c3"),
        ],
        target_score=95.0,
    )
    # c1 accepted (90), c2 accepted (95 = target) → stops after c2.
    assert len(results) == 2
    assert results[-1].accepted and results[-1].candidate_score == 95.0


def test_run_loop_respects_max_rounds():
    def evaluator(candidate):
        return 100.0  # everything improves

    opt = ArtifactOptimizer(reference="base", reference_score=50.0, evaluator=evaluator)
    results = opt.run_loop(
        [OptimizerCandidate(f"c{i}") for i in range(5)],
        max_rounds=3,
    )
    assert len(results) == 3


def test_candidate_versioning():
    c1 = OptimizerCandidate("change", version=1)
    c2 = OptimizerCandidate("change", version=2)
    assert c1.version == 1 and c2.version == 2
    assert c1 != c2  # different versions are distinct candidates


def test_accept_updates_reference_payload():
    accepted = []

    def evaluator(candidate):
        return 90.0

    opt = ArtifactOptimizer(
        reference="base",
        reference_score=80.0,
        evaluator=evaluator,
        apply_candidate=accepted.append,
    )
    cand = OptimizerCandidate("new state", payload={"prompt": "v2"})
    result = opt.run_round(cand)
    assert result.accepted
    assert opt.reference is cand  # candidate became the Reference
    assert accepted == [cand]
