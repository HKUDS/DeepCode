"""P2-F1 (GenAI lesson 19): SLM/LLM task-complexity routing.

Lesson 19: small language models (SLM — Mistral 7B, Phi-3) fit local /
edge / low-cost niches. DeepCode already routes by reasoning effort
(``core.providers.reasoning``) and uses a small classifier model for risk
gating; this module adds an explicit *subtask-class* router: high-frequency,
low-complexity subtasks (tool-result cleanup, summarization, classification)
should ride the SLM path, while deep reasoning stays on the LLM path.

Pure decision mechanism: ``route_subtask(task_class, ...) -> RoutingDecision``
with env-tunable model overrides. No I/O.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# Subtask classes with an inherent complexity tier (lesson 19: route by task
# complexity, not by caller identity).
SUBTASK_SIMPLE = "simple"  # classification, extraction, cleanup, formatting
SUBTASK_MEDIUM = "medium"  # summarization, translation, structured rewrite
SUBTASK_COMPLEX = "complex"  # planning, debugging, multi-step reasoning

# Default tier per class (SLM for simple/medium; LLM for complex).
_TIER_BY_CLASS = {
    SUBTASK_SIMPLE: "slm",
    SUBTASK_MEDIUM: "slm",
    SUBTASK_COMPLEX: "llm",
}

_KNOWN_CLASSES = frozenset(_TIER_BY_CLASS)

# Env override: DEEPCODE_SLM_MODEL / DEEPCODE_LLM_MODEL — the router is
# environment-driven so deployments pick their own SLM/LLM pair.
# DEEPCODE_SLM_ROUTING=0 disables SLM routing (everything → llm tier).


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """One subtask's routing decision."""

    task_class: str
    tier: Literal["slm", "llm"]
    model: str | None
    reason: str
    override: bool = False

    def to_dict(self) -> dict[str, str | None | bool]:
        return {
            "task_class": self.task_class,
            "tier": self.tier,
            "model": self.model,
            "reason": self.reason,
            "override": self.override,
        }


def slm_routing_enabled() -> bool:
    """Whether SLM routing is on (env ``DEEPCODE_SLM_ROUTING``; default on)."""
    value = os.environ.get("DEEPCODE_SLM_ROUTING", "").strip().lower()
    if not value:
        return True
    return value not in {"0", "false", "off", "no"}


def slm_model() -> str | None:
    """Configured SLM model id (env ``DEEPCODE_SLM_MODEL``), or None."""
    value = os.environ.get("DEEPCODE_SLM_MODEL", "").strip()
    return value or None


def llm_model() -> str | None:
    """Configured LLM model id (env ``DEEPCODE_LLM_MODEL``), or None."""
    value = os.environ.get("DEEPCODE_LLM_MODEL", "").strip()
    return value or None


def route_subtask(
    task_class: str,
    *,
    default_model: str | None = None,
    slm_override: str | None = None,
    llm_override: str | None = None,
) -> RoutingDecision:
    """Route one subtask to the SLM or LLM tier.

    Parameters
    ----------
    task_class:
        One of the ``SUBTASK_*`` constants (unknown classes default to
        ``llm`` with a note — safer to over-provision than to under-reason).
    default_model:
        The session's current model; returned for the llm tier when no
        explicit LLM override is set.
    slm_override / llm_override:
        Explicit model ids (win over env; env wins over None).
    """
    task_class = str(task_class or "").strip()
    if task_class not in _KNOWN_CLASSES:
        return RoutingDecision(
            task_class=task_class or "unknown",
            tier="llm",
            model=llm_override or llm_model() or default_model,
            reason=f"unknown task class {task_class!r}; defaulting to LLM",
        )
    if not slm_routing_enabled():
        return RoutingDecision(
            task_class=task_class,
            tier="llm",
            model=llm_override or llm_model() or default_model,
            reason="SLM routing disabled (DEEPCODE_SLM_ROUTING=0)",
            override=True,
        )
    tier = _TIER_BY_CLASS[task_class]
    if tier == "slm":
        model = slm_override or slm_model() or None
        if model is None:
            return RoutingDecision(
                task_class=task_class,
                tier="llm",
                model=llm_override or llm_model() or default_model,
                reason=(
                    "SLM tier requested but DEEPCODE_SLM_MODEL unset; "
                    "falling back to LLM"
                ),
            )
        return RoutingDecision(
            task_class=task_class,
            tier="slm",
            model=model,
            reason=f"{task_class} subtask is low-complexity; routing to SLM",
        )
    return RoutingDecision(
        task_class=task_class,
        tier="llm",
        model=llm_override or llm_model() or default_model,
        reason=f"{task_class} subtask needs deep reasoning; routing to LLM",
    )


__all__ = [
    "SUBTASK_COMPLEX",
    "SUBTASK_MEDIUM",
    "SUBTASK_SIMPLE",
    "RoutingDecision",
    "llm_model",
    "route_subtask",
    "slm_model",
    "slm_routing_enabled",
]
