"""Tests for the cerebellum end-to-end skill optimization loop (step 2)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop import cerebellum_optimizer as co
from core.loop.optimizer import OptimizerCandidate

# ---- fake cerebellum module -------------------------------------------------


class FakeCerebellum:
    """A stand-in for cerebellum_evolution with scriptable scores."""

    DEFAULT_DB = Path("fake.db")

    def __init__(self):
        self.scores = [0.50]  # queue of benchmark MRR results
        self.proposals = []
        self.applied = []
        self.rejected = []
        self.benchmark_calls = 0

    # -- benchmark_run -----------------------------------------------------

    def benchmark_run(self, db_path=None, top_k=5):
        self.benchmark_calls += 1
        score = self.scores.pop(0) if len(self.scores) > 1 else self.scores[0]
        return {
            "ok": True,
            "metrics": {
                "mrr": score,
                "recall@1": score * 0.8,
                "top_k": top_k,
                "queries": 10,
            },
        }

    # -- proposals -----------------------------------------------------------

    def skill_evolution_list(self, status=None, db_path=None, limit=20):
        return {"ok": True, "proposals": list(self.proposals)}

    def skill_evolution_apply(self, proposal_id, db_path=None):
        self.applied.append(proposal_id)
        return {"ok": True, "proposal_id": proposal_id, "skill_name": "fake-skill"}

    def skill_evolution_reject(self, proposal_id, db_path=None):
        self.rejected.append(proposal_id)
        return {"ok": True, "proposal_id": proposal_id, "status": "rejected"}

    # -- skill path -----------------------------------------------------------

    def _skill_md_path(self, skill_name):
        return _FAKE_SKILL_MD


# A shared fake SKILL.md location, created per-test in tmp_path.
_FAKE_SKILL_MD: Path | None = None


def _install_fake(monkeypatch, tmp_path):
    global _FAKE_SKILL_MD
    _FAKE_SKILL_MD = tmp_path / "SKILL.md"
    _FAKE_SKILL_MD.write_text("base skill content\n", encoding="utf-8")
    fake = FakeCerebellum()
    monkeypatch.setattr(co, "_import_cerebellum", lambda: fake)
    return fake, _FAKE_SKILL_MD


# ---- tests ------------------------------------------------------------------


def test_import_cerebellum_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_CEREBELLUM_EVOLUTION", tmp_path / "nope.py")
    try:
        co._import_cerebellum()
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_no_pending_proposals(monkeypatch, tmp_path):
    fake, _ = _install_fake(monkeypatch, tmp_path)
    fake.proposals = []
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once()
    assert outcomes == []


def test_accepts_when_mrr_improves(monkeypatch, tmp_path):
    fake, md = _install_fake(monkeypatch, tmp_path)
    # Benchmark scores: before=0.50, after=0.70 → strictly higher → accept.
    fake.scores = [0.50, 0.70]
    fake.proposals = [
        {"id": 7, "skill_name": "fake-skill", "suggested_change": "add examples"}
    ]
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once()
    assert len(outcomes) == 1
    out = outcomes[0]
    assert out.accepted is True
    assert out.proposal_id == 7
    assert out.score_before == 0.50 and out.score_after == 0.70
    assert fake.applied == [7]
    assert fake.rejected == []  # accepted → not rejected
    # The apply hook was invoked; the real cerebellum writes the section.
    # (Fake apply doesn't touch the file, so we assert the protocol state.)
    assert "base skill content" in md.read_text(encoding="utf-8")


def test_rolls_back_when_mrr_not_improved(monkeypatch, tmp_path):
    fake, md = _install_fake(monkeypatch, tmp_path)
    # Benchmark scores: before=0.50, after=0.50 (not strictly higher) → rollback.
    fake.scores = [0.50, 0.50]
    fake.proposals = [
        {"id": 8, "skill_name": "fake-skill", "suggested_change": "rewrite"}
    ]
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once()
    assert len(outcomes) == 1
    out = outcomes[0]
    assert out.accepted is False
    assert fake.applied == [8]
    assert fake.rejected == [8]
    # SKILL.md restored to its pre-apply content (no evolution section).
    assert "base skill content" in md.read_text(encoding="utf-8")
    assert "进化记录" not in md.read_text(encoding="utf-8")


def test_rolls_back_when_benchmark_fails(monkeypatch, tmp_path):
    fake, md = _install_fake(monkeypatch, tmp_path)
    # Before ok, after fails (None) → not improved → rollback.
    fake.scores = [0.50, None]

    def broken_benchmark(db_path=None, top_k=5):
        fake.benchmark_calls += 1
        if fake.benchmark_calls == 2:
            return {"ok": False, "error": "empty QA set"}
        return {
            "ok": True,
            "metrics": {
                "mrr": fake.scores.pop(0) if fake.scores else 0.5,
                "top_k": top_k,
                "queries": 5,
            },
        }

    fake.benchmark_run = broken_benchmark
    fake.proposals = [{"id": 9, "skill_name": "fake-skill", "suggested_change": "x"}]
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once()
    assert outcomes[0].accepted is False
    assert fake.rejected == [9]
    assert "进化记录" not in md.read_text(encoding="utf-8")


def test_missing_skill_md_skips(monkeypatch, tmp_path):
    fake, _ = _install_fake(monkeypatch, tmp_path)
    fake.scores = [0.50, 0.70]
    fake.proposals = [{"id": 10, "skill_name": "ghost"}]

    def no_path(skill_name):
        return None

    fake._skill_md_path = no_path
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once()
    assert len(outcomes) == 1
    assert outcomes[0].accepted is False
    assert outcomes[0].reason == "SKILL.md not found"
    assert fake.applied == []  # never applied


def test_min_delta_gate(monkeypatch, tmp_path):
    fake, _ = _install_fake(monkeypatch, tmp_path)
    # +0.1 improvement but min_delta=0.2 → not accepted.
    fake.scores = [0.50, 0.60]
    fake.proposals = [{"id": 11, "skill_name": "fake-skill", "suggested_change": "x"}]
    opt = co.CerebellumSkillOptimizer()
    outcomes = opt.run_once(min_delta=0.2)
    assert outcomes[0].accepted is False
    assert fake.rejected == [11]


def test_evaluator_returns_none_on_failure(monkeypatch, tmp_path):
    fake, _ = _install_fake(monkeypatch, tmp_path)

    def broken(db_path=None, top_k=5):
        return {"ok": False, "error": "boom"}

    fake.benchmark_run = broken
    ev = co.CerebellumBenchmarkEvaluator()
    assert ev.current_score() is None
    assert ev(OptimizerCandidate("x")) is None
