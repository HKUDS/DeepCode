"""Shared implementation-plan review runtime.

The core workflow owns plan validation, persistence, versioning, and AI
revision. UI/CLI layers only collect a user's decision for the current plan.
Keeping that boundary here prevents the frontends from growing separate plan
mutation logic and keeps concurrent sessions isolated by their task directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.compat import Agent, RequestParams
from core.llm_runtime import attach_workflow_llm
from utils.llm_utils import get_token_limits
from workflows.planning_runtime import (
    append_jsonl,
    extract_yaml_candidate,
    read_planning_meta,
    utc_now_iso,
    validate_plan_text,
    write_planning_meta,
)


PlanReviewCallback = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_DEFAULT_MAX_REVIEW_ROUNDS = 3
_DEFAULT_PLAN_REVIEW_TIMEOUT_S = 180


class PlanReviewCancelled(RuntimeError):
    """Raised when the user cancels the workflow at plan review time."""

    def __init__(self, reason: str = "User cancelled at plan review") -> None:
        super().__init__(reason)
        self.reason = reason


def plan_review_paths(paper_dir: str | Path) -> dict[str, Path]:
    root = Path(paper_dir)
    return {
        "history": root / "plan_review_history.jsonl",
        "versions_dir": root / "plan_versions",
    }


def read_plan_file(initial_plan_path: str | Path) -> str:
    return Path(initial_plan_path).read_text(encoding="utf-8")


def write_plan_file(initial_plan_path: str | Path, plan_text: str) -> None:
    path = Path(initial_plan_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(plan_text, encoding="utf-8")
    tmp.replace(path)


def append_plan_review_event(paper_dir: str | Path, payload: dict[str, Any]) -> None:
    append_jsonl(
        plan_review_paths(paper_dir)["history"],
        {
            "timestamp": utc_now_iso(),
            **payload,
        },
    )


def save_plan_version(
    paper_dir: str | Path,
    plan_text: str,
    *,
    version: int,
    label: str,
) -> Path:
    paths = plan_review_paths(paper_dir)
    versions_dir = paths["versions_dir"]
    versions_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label)
    target = versions_dir / f"initial_plan.v{version:02d}.{safe_label}.txt"
    target.write_text(plan_text, encoding="utf-8")
    return target


def update_plan_review_meta(paper_dir: str | Path, **updates: Any) -> None:
    current = read_planning_meta(paper_dir) or {}
    review = dict(current.get("plan_review") or {})
    review.update(updates)
    review["updated_at"] = utc_now_iso()
    write_planning_meta(
        paper_dir,
        {
            **current,
            "plan_review": review,
        },
    )


def _plan_review_timeout_s() -> int:
    raw = os.environ.get("DEEPCODE_PLAN_REVIEW_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_PLAN_REVIEW_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PLAN_REVIEW_TIMEOUT_S
    return value if value > 0 else _DEFAULT_PLAN_REVIEW_TIMEOUT_S


def _normalise_action(decision: dict[str, Any] | None) -> str:
    if not decision:
        return "approve"
    if decision.get("skipped"):
        return "skip"
    action = str(decision.get("action") or "").strip().lower()
    aliases = {
        "confirm": "approve",
        "approved": "approve",
        "continue": "approve",
        "replace_plan": "replace",
        "edit": "replace",
        "manual_edit": "replace",
        "timeout": "skip",
    }
    return aliases.get(action, action or "approve")


def _decision_value(decision: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in decision:
            return decision[name]
    data = decision.get("data")
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
    return None


def _validation_error(validation: dict[str, Any]) -> str:
    missing = validation.get("missing_sections") or []
    yaml_error = validation.get("yaml_error")
    parts = []
    if missing:
        parts.append(f"missing required sections: {missing}")
    if yaml_error:
        parts.append(f"YAML issue: {yaml_error}")
    return "; ".join(parts) or "plan failed validation"


def _build_revision_prompt(
    current_plan: str,
    feedback: str,
    *,
    previous_error: str | None = None,
) -> str:
    validation_hint = ""
    if previous_error:
        validation_hint = (
            "\nThe previous revision failed validation. Fix this issue exactly:\n"
            f"{previous_error}\n"
        )

    return f"""Revise the implementation plan according to the user's feedback.

Rules:
- Output the full revised plan only.
- Preserve valid YAML structure.
- Keep these required sections: file_structure, implementation_components,
  validation_approach, environment_setup, implementation_strategy.
- Do not wrap the answer in markdown fences.
- Do not drop implementation detail that is unrelated to the requested change.
{validation_hint}
User feedback:
{feedback}

Current plan:
{current_plan}
"""


async def revise_plan_with_feedback(
    current_plan: str,
    feedback: str,
    *,
    paper_dir: str | Path,
    logger: Any = None,
    max_retries: int = 2,
) -> str:
    """Return a validated YAML plan revised from natural-language feedback."""
    feedback = (feedback or "").strip()
    if not feedback:
        raise ValueError("Plan modification requires non-empty feedback")

    base_max_tokens, _ = get_token_limits()
    max_tokens = max(base_max_tokens, min(max(len(current_plan) // 2, 4096), 32000))
    timeout_s = _plan_review_timeout_s()
    previous_error: str | None = None

    for attempt in range(1, max_retries + 1):
        agent = Agent(
            name=f"PlanRevisionAgent-{Path(paper_dir).name}",
            instruction=(
                "You are a careful reproduction-plan editor. You modify an existing "
                "YAML implementation plan while preserving its schema and technical "
                "specificity."
            ),
            server_names=[],
        )
        params = RequestParams(
            maxTokens=max_tokens,
            temperature=0.1,
            max_iterations=1,
            llm_timeout_s=timeout_s,
            enforce_default_max_iterations=False,
        )
        prompt = _build_revision_prompt(
            current_plan,
            feedback,
            previous_error=previous_error,
        )

        async with agent:
            llm = await attach_workflow_llm(agent, phase="planning")
            raw = await llm.generate_str(message=prompt, request_params=params)

        revised = extract_yaml_candidate(raw).strip()
        validation = validate_plan_text(revised)
        append_plan_review_event(
            paper_dir,
            {
                "event": "revision_attempt",
                "attempt": attempt,
                "plan_chars": len(revised),
                "validation": validation,
            },
        )
        if validation.get("valid", False):
            return revised

        previous_error = _validation_error(validation)
        if logger:
            logger.warning(
                f"Plan revision attempt {attempt}/{max_retries} failed validation: "
                f"{previous_error}"
            )

    raise ValueError(previous_error or "Plan revision failed validation")


def _build_review_request(
    *,
    paper_dir: str | Path,
    initial_plan_path: str | Path,
    plan: str,
    validation: dict[str, Any],
    modification_round: int,
    max_rounds: int,
    last_error: str | None,
) -> dict[str, Any]:
    lines = plan.splitlines()
    preview = "\n".join(lines[:80])
    if len(lines) > 80:
        preview += f"\n... ({len(lines) - 80} more lines)"

    description = (
        "Review the generated implementation plan before code generation starts."
    )
    if modification_round:
        description = (
            f"Review the modified implementation plan "
            f"(round {modification_round}/{max_rounds})."
        )
    if last_error:
        description = f"{description} Last issue: {last_error}"

    return {
        "interaction_type": "plan_review",
        "title": "Review Implementation Plan",
        "description": description,
        "required": False,
        "timeout_seconds": 1800,
        "data": {
            "plan": plan,
            "plan_preview": preview,
            "plan_path": str(initial_plan_path),
            "paper_dir": str(paper_dir),
            "modification_round": modification_round,
            "max_rounds": max_rounds,
            "plan_validation": validation,
            "last_error": last_error,
        },
        "options": {
            "confirm": "Approve & Continue",
            "modify": "Request Changes",
            "replace": "Replace Plan",
            "cancel": "Cancel Workflow",
        },
    }


async def run_plan_review_gate(
    *,
    initial_plan_path: str | Path,
    paper_dir: str | Path,
    callback: PlanReviewCallback | None,
    logger: Any = None,
    max_rounds: int = _DEFAULT_MAX_REVIEW_ROUNDS,
) -> dict[str, Any]:
    """Pause after plan generation, optionally revise, and return final status."""
    path = Path(initial_plan_path)
    if callback is None:
        update_plan_review_meta(
            paper_dir,
            status="skipped",
            enabled=False,
            reason="no_plan_review_callback",
        )
        return {"status": "skipped", "reason": "no_plan_review_callback"}

    plan = read_plan_file(path)
    validation = validate_plan_text(plan)

    save_plan_version(paper_dir, plan, version=0, label="generated")
    append_plan_review_event(
        paper_dir,
        {
            "event": "review_started",
            "initial_plan_path": str(path),
            "plan_chars": len(plan),
            "validation": validation,
        },
    )
    update_plan_review_meta(
        paper_dir,
        status="waiting_for_review",
        enabled=True,
        rounds=0,
        approved=False,
        initial_plan_path=str(path),
    )

    modification_round = 0
    interaction_count = 0
    max_interactions = max_rounds + 4
    last_error: str | None = None

    while interaction_count < max_interactions:
        interaction_count += 1
        validation = validate_plan_text(plan)
        request = _build_review_request(
            paper_dir=paper_dir,
            initial_plan_path=path,
            plan=plan,
            validation=validation,
            modification_round=modification_round,
            max_rounds=max_rounds,
            last_error=last_error,
        )

        append_plan_review_event(
            paper_dir,
            {
                "event": "review_requested",
                "interaction": interaction_count,
                "round": modification_round,
                "validation": validation,
            },
        )
        decision = await callback(request)
        action = _normalise_action(decision)
        append_plan_review_event(
            paper_dir,
            {
                "event": "review_response",
                "interaction": interaction_count,
                "round": modification_round,
                "action": action,
                "skipped": bool(decision and decision.get("skipped")),
            },
        )

        if action in {"approve", "skip"}:
            final_validation = validate_plan_text(plan)
            update_plan_review_meta(
                paper_dir,
                status="approved",
                approved=True,
                auto_approved=(action == "skip"),
                rounds=modification_round,
                interactions=interaction_count,
                final_plan_chars=len(plan),
                final_validation=final_validation,
                approved_at=utc_now_iso(),
            )
            append_plan_review_event(
                paper_dir,
                {
                    "event": "review_approved",
                    "action": action,
                    "round": modification_round,
                    "validation": final_validation,
                },
            )
            return {
                "status": "approved",
                "action": action,
                "rounds": modification_round,
                "interactions": interaction_count,
                "plan_validation": final_validation,
                "initial_plan_path": str(path),
            }

        if action == "cancel":
            reason = str(
                _decision_value(decision or {}, "reason")
                or "User cancelled at plan review"
            )
            update_plan_review_meta(
                paper_dir,
                status="cancelled",
                approved=False,
                rounds=modification_round,
                interactions=interaction_count,
                cancel_reason=reason,
            )
            append_plan_review_event(
                paper_dir,
                {
                    "event": "review_cancelled",
                    "reason": reason,
                    "round": modification_round,
                },
            )
            raise PlanReviewCancelled(reason)

        if action == "replace":
            replacement = _decision_value(decision or {}, "plan", "replacement_plan")
            replacement = str(replacement or "").strip()
            if not replacement:
                last_error = "Replacement plan was empty"
                write_plan_file(path, plan)
                continue
            replacement_validation = validate_plan_text(replacement)
            if not replacement_validation.get("valid", False):
                last_error = _validation_error(replacement_validation)
                write_plan_file(path, plan)
                append_plan_review_event(
                    paper_dir,
                    {
                        "event": "replacement_rejected",
                        "round": modification_round,
                        "validation": replacement_validation,
                    },
                )
                continue

            modification_round += 1
            plan = extract_yaml_candidate(replacement).strip()
            write_plan_file(path, plan)
            save_plan_version(
                paper_dir,
                plan,
                version=modification_round,
                label="manual",
            )
            update_plan_review_meta(
                paper_dir,
                status="modified",
                approved=False,
                rounds=modification_round,
                last_action="replace",
                last_validation=replacement_validation,
            )
            last_error = None
            continue

        if action == "modify":
            feedback = str(_decision_value(decision or {}, "feedback") or "").strip()
            if not feedback:
                last_error = "Modification feedback was empty"
                continue
            if modification_round >= max_rounds:
                last_error = (
                    f"Maximum modification rounds reached ({max_rounds}); "
                    "approve, replace manually, or cancel."
                )
                continue

            update_plan_review_meta(
                paper_dir,
                status="revision_running",
                approved=False,
                rounds=modification_round,
                last_feedback=feedback,
            )
            try:
                revised = await revise_plan_with_feedback(
                    plan,
                    feedback,
                    paper_dir=paper_dir,
                    logger=logger,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                append_plan_review_event(
                    paper_dir,
                    {
                        "event": "revision_failed",
                        "round": modification_round,
                        "error": last_error,
                    },
                )
                continue

            modification_round += 1
            plan = revised
            write_plan_file(path, plan)
            revised_validation = validate_plan_text(plan)
            save_plan_version(
                paper_dir,
                plan,
                version=modification_round,
                label="ai",
            )
            update_plan_review_meta(
                paper_dir,
                status="modified",
                approved=False,
                rounds=modification_round,
                last_action="modify",
                last_feedback=feedback,
                last_validation=revised_validation,
            )
            last_error = None
            continue

        last_error = f"Unknown review action: {action}"

    update_plan_review_meta(
        paper_dir,
        status="approved",
        approved=True,
        auto_approved=True,
        rounds=modification_round,
        interactions=interaction_count,
        warning="review interaction limit reached; continuing with latest valid plan",
    )
    append_plan_review_event(
        paper_dir,
        {
            "event": "review_auto_approved",
            "reason": "interaction_limit_reached",
            "round": modification_round,
        },
    )
    return {
        "status": "approved",
        "action": "auto_approve",
        "rounds": modification_round,
        "interactions": interaction_count,
        "warning": "review interaction limit reached",
        "initial_plan_path": str(path),
    }
