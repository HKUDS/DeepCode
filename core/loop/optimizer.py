"""P0-3: evidence → hypothesis → candidate → evaluate → accept/rollback
optimization loop (PenguinHarness agent-optimization lesson).

PenguinHarness' ``agent-optimization`` skill runs a disciplined loop: keep a
*Reference* (the current best agent state + its measured score), test bounded
*Candidates* against a frozen benchmark, and accept a Candidate **only when**
its score is *strictly higher* than the Reference — otherwise restore the
Reference (versioned snapshots protect against regressions). Contamination is
forbidden (no looking at private evaluation data), and every evaluation is
delegated rather than run by the optimizer.

DeepCode already has failure-signal-driven skill evolution in cerebellum
(skill_signals → LLM analysis → SKILL.md proposal → human apply), but no
measurement-driven accept/rollback loop. This module supplies the *pure
mechanism* of that loop: the decision protocol, independent of any specific
artifact (skills, prompts, configs). Evaluation is injected — the optimizer
never runs the subject directly.

Design rules (mirrors ``core.harness``): pure mechanism; no LLM, no
subprocess. The loop protocol is:

1. Reference = best known state + its measured score.
2. A Candidate is a bounded, general change from the Reference.
3. Evaluate the Candidate (injected evaluator) → its score.
4. Accept only if score is *strictly* higher than Reference; otherwise
   roll back to the Reference.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Evaluator: (candidate) -> score (float, higher is better). Returns None when
# the evaluation is invalid/incomplete (treated as "do not accept").
Evaluator = Callable[[Any], float | None]


@dataclass
class OptimizerCandidate:
    """One bounded, general change to test against the Reference."""

    description: str
    version: int = 1
    payload: Any = None  # the change itself (skill text, prompt, config, ...)


@dataclass
class OptimizationResult:
    """The outcome of one candidate round."""

    accepted: bool
    candidate: OptimizerCandidate
    reference_score: float | None = None
    candidate_score: float | None = None
    reason: str = ""

    @property
    def improved(self) -> bool:
        return self.accepted and self.candidate_score is not None


class ArtifactOptimizer:
    """Run the accept/rollback protocol over candidates of one artifact.

    Parameters
    ----------
    reference:
        The current best state (any object; passed through to the evaluator
        and rollback hook).
    reference_score:
        The Reference's measured score on the frozen benchmark.
    evaluator:
        ``(candidate) -> score | None`` — delegated measurement. Returning
        None (invalid/incomplete evaluation) means "do not accept".
    apply_candidate:
        Optional hook ``(candidate)`` called when a candidate is accepted,
        to make it the new Reference on disk.
    rollback:
        Optional hook ``(reference)`` called when a candidate is rejected, to
        restore the previous Reference.
    """

    def __init__(
        self,
        reference: Any,
        reference_score: float,
        *,
        evaluator: Evaluator,
        apply_candidate: Callable[[Any], None] | None = None,
        rollback: Callable[[Any], None] | None = None,
    ) -> None:
        self.reference = reference
        self.reference_score = reference_score
        self._evaluator = evaluator
        self._apply = apply_candidate
        self._rollback = rollback

    def run_round(
        self,
        candidate: OptimizerCandidate,
        *,
        min_delta: float = 0.0,
    ) -> OptimizationResult:
        """Evaluate one candidate and accept or roll back.

        ``min_delta`` is the minimum improvement required to accept (default
        0 — any strictly higher score accepts). A candidate whose evaluation
        is invalid (None) or not strictly higher is rejected and the previous
        Reference is restored via the rollback hook.
        """
        score = self._evaluator(candidate)
        if score is None:
            self._rollback_if_present()
            return OptimizationResult(
                accepted=False,
                candidate=candidate,
                reference_score=self.reference_score,
                candidate_score=None,
                reason="evaluation invalid/incomplete; not accepted",
            )
        improved = score > self.reference_score + min_delta
        if improved:
            self.reference = candidate
            self.reference_score = score
            if self._apply is not None:
                self._apply(candidate)
            return OptimizationResult(
                accepted=True,
                candidate=candidate,
                reference_score=self.reference_score,
                candidate_score=score,
                reason="score strictly higher; accepted",
            )
        self._rollback_if_present()
        return OptimizationResult(
            accepted=False,
            candidate=candidate,
            reference_score=self.reference_score,
            candidate_score=score,
            reason=(
                f"score {score} not strictly higher than reference "
                f"{self.reference_score}; rolled back"
            ),
        )

    def run_loop(
        self,
        candidates: list[OptimizerCandidate],
        *,
        target_score: float | None = None,
        max_rounds: int | None = None,
        min_delta: float = 0.0,
    ) -> list[OptimizationResult]:
        """Run candidates in order; stop early when the target is reached."""
        results: list[OptimizationResult] = []
        limit = max_rounds if max_rounds is not None else len(candidates)
        for i, candidate in enumerate(candidates[:limit]):
            result = self.run_round(candidate, min_delta=min_delta)
            results.append(result)
            if target_score is not None and self.reference_score >= target_score:
                break
        return results

    def _rollback_if_present(self) -> None:
        try:
            if self._rollback is not None:
                self._rollback(self.reference)
        except Exception:  # noqa: BLE001, S110 - rollback is best-effort
            pass


__all__ = [
    "ArtifactOptimizer",
    "OptimizationResult",
    "OptimizerCandidate",
]
