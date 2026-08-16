"""P0-5: cerebellum end-to-end self-evolution loop (step 2 of the plan).

Wires the accept/rollback protocol from :mod:`core.loop.optimizer` to the
cerebellum memory system:

* **Evaluator** — cerebellum's ``benchmark_run`` (retrieval self-benchmark on
  the semantic index, Recall@1 / MRR) provides the *score* for a candidate:
  applying a skill-evolution proposal changes the memory index → re-run the
  benchmark → a higher MRR means the change helped retrieval.
* **Candidates** — pending skill-evolution proposals (``skill_evolution_list``)
  are the candidate source; each proposal's ``suggested_change`` becomes an
  :class:`OptimizerCandidate`.
* **Apply / rollback** — cerebellum's ``skill_evolution_apply`` appends an
  "进化记录" section to SKILL.md (never rewrites the original), so rollback
  is exact: truncate the file back to its pre-apply length and mark the
  proposal rejected.

The loop: for each pending proposal → snapshot SKILL.md length → apply →
benchmark → accept (proposal stays applied) iff MRR is strictly higher →
else rollback (truncate + reject). This is the "评测→优化→回滚" closed loop.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from core.loop.optimizer import (
    OptimizerCandidate,
)

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
    import importlib.util

    spec = importlib.util.spec_from_file_location("cerebellum_evolution", module)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class CerebellumBenchmarkEvaluator:
    """Score a candidate by cerebellum's retrieval benchmark (MRR).

    The candidate's payload is a description only; the real measurement is
    the benchmark run against the *current* memory index (which the apply
    step has already mutated by the time evaluation happens).
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        top_k: int = 5,
        metric: str = "mrr",
    ) -> None:
        self._db_path = db_path
        self._top_k = top_k
        self._metric = metric
        self._mod: Any | None = None

    def _module(self) -> Any:
        if self._mod is None:
            self._mod = _import_cerebellum()
        return self._mod

    def current_score(self) -> float | None:
        """Run the benchmark and return the metric (None on failure/empty)."""
        try:
            mod = self._module()
            result = mod.benchmark_run(
                db_path=self._db_path or mod.DEFAULT_DB,
                top_k=self._top_k,
            )
            if not result.get("ok"):
                return None
            metrics = result.get("metrics", {})
            value = metrics.get(self._metric)
            return float(value) if isinstance(value, (int, float)) else None
        except Exception:  # noqa: BLE001 - measurement must never crash the loop
            logger.debug("cerebellum benchmark failed", exc_info=True)
            return None

    def __call__(self, candidate: OptimizerCandidate) -> float | None:
        """Evaluator contract: score the (already-applied) candidate."""
        return self.current_score()


# ---------------------------------------------------------------------------
# Skill optimizer: proposals → candidates → apply → benchmark → accept/rollback
# ---------------------------------------------------------------------------


@dataclass
class SkillOptimizationOutcome:
    """Outcome of optimizing one skill proposal."""

    proposal_id: int
    skill_name: str
    accepted: bool
    score_before: float | None
    score_after: float | None
    reason: str


class CerebellumSkillOptimizer:
    """Run the accept/rollback protocol over pending skill proposals.

    Parameters
    ----------
    db_path:
        Cerebellum DB path (default: cerebellum's own DEFAULT_DB).
    top_k / metric:
        Benchmark parameters for the evaluator.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        top_k: int = 5,
        metric: str = "mrr",
    ) -> None:
        self._db_path = db_path
        self._top_k = top_k
        self._metric = metric
        self._mod: Any | None = None

    def _module(self) -> Any:
        if self._mod is None:
            self._mod = _import_cerebellum()
        return self._mod

    def pending_proposals(self, limit: int = 20) -> list[dict]:
        """Pending skill-evolution proposals as candidate descriptors."""
        try:
            mod = self._module()
            result = mod.skill_evolution_list(
                status="pending",
                db_path=self._db_path or mod.DEFAULT_DB,
                limit=limit,
            )
            proposals = result.get("proposals") or result.get("items") or []
            return list(proposals)
        except Exception:  # noqa: BLE001
            logger.debug("pending proposals list failed", exc_info=True)
            return []

    def _skill_md_path(self, skill_name: str) -> Path | None:
        try:
            mod = self._module()
            p = mod._skill_md_path(skill_name)
            return Path(p) if p else None
        except Exception:  # noqa: BLE001
            return None

    def _apply_proposal(self, proposal_id: int) -> str | None:
        """Apply a proposal; returns the SKILL.md path (None on failure)."""
        try:
            mod = self._module()
            result = mod.skill_evolution_apply(
                proposal_id, db_path=self._db_path or mod.DEFAULT_DB
            )
            if result.get("ok"):
                return result.get("applied_to") or result.get("skill_name")
            return None
        except Exception:  # noqa: BLE001
            logger.debug("proposal apply failed", exc_info=True)
            return None

    def _reject_proposal(self, proposal_id: int) -> None:
        try:
            mod = self._module()
            mod.skill_evolution_reject(
                proposal_id, db_path=self._db_path or mod.DEFAULT_DB
            )
        except Exception:  # noqa: BLE001, S110
            pass

    def run_once(self, *, min_delta: float = 0.0) -> list[SkillOptimizationOutcome]:
        """Evaluate all pending proposals once: apply → benchmark → accept or
        roll back. Returns per-proposal outcomes."""
        proposals = self.pending_proposals()
        if not proposals:
            logger.info("cerebellum skill optimizer: no pending proposals")
            return []
        evaluator = CerebellumBenchmarkEvaluator(
            self._db_path, top_k=self._top_k, metric=self._metric
        )
        outcomes: list[SkillOptimizationOutcome] = []
        for proposal in proposals:
            pid = int(proposal.get("id") or 0)
            skill = str(proposal.get("skill_name") or "")
            if not pid or not skill:
                continue
            score_before = evaluator.current_score()
            md_path = self._skill_md_path(skill)
            if md_path is None:
                outcomes.append(
                    SkillOptimizationOutcome(
                        pid,
                        skill,
                        False,
                        score_before,
                        None,
                        "SKILL.md not found",
                    )
                )
                continue
            original_size = md_path.stat().st_size if md_path.exists() else 0

            applied = self._apply_proposal(pid)
            if applied is None:
                outcomes.append(
                    SkillOptimizationOutcome(
                        pid,
                        skill,
                        False,
                        score_before,
                        None,
                        "apply failed",
                    )
                )
                continue

            score_after = evaluator.current_score()
            improved = (
                score_after is not None
                and score_before is not None
                and score_after > score_before + min_delta
            )
            if improved:
                logger.info(
                    "skill {} proposal #{} ACCEPTED ({} {:.4f} → {:.4f})",
                    skill,
                    pid,
                    self._metric,
                    score_before,
                    score_after,
                )
                outcomes.append(
                    SkillOptimizationOutcome(
                        pid,
                        skill,
                        True,
                        score_before,
                        score_after,
                        "MRR strictly higher; proposal kept",
                    )
                )
            else:
                # Rollback: truncate SKILL.md back to pre-apply size + reject.
                try:
                    if md_path.exists() and md_path.stat().st_size > original_size:
                        with open(md_path, "r", encoding="utf-8") as fh:
                            content = fh.read()
                        with open(md_path, "w", encoding="utf-8") as fh:
                            fh.write(content[:original_size])
                except Exception:  # noqa: BLE001
                    logger.debug("rollback truncate failed for {}", skill)
                self._reject_proposal(pid)
                logger.info(
                    "skill {} proposal #{} ROLLED BACK ({} {:.4f} → {})",
                    skill,
                    pid,
                    self._metric,
                    score_before,
                    f"{score_after:.4f}" if score_after is not None else "n/a",
                )
                outcomes.append(
                    SkillOptimizationOutcome(
                        pid,
                        skill,
                        False,
                        score_before,
                        score_after,
                        "MRR not strictly higher; rolled back",
                    )
                )
        return outcomes


__all__ = [
    "CerebellumBenchmarkEvaluator",
    "CerebellumSkillOptimizer",
    "SkillOptimizationOutcome",
]
