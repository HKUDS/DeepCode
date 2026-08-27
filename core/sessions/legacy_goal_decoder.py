"""Read-only decoder for the pre-v2 Goal ledger.

The legacy runtime wrote Goal snapshots plus Attempt/Evaluation/Decision
records.  Only the latest Goal snapshot is needed by the v2 Thread extension;
the auxiliary records are validated for ownership and otherwise ignored.
Nothing in this module writes data or participates in execution.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.domain.thread_goal import ThreadGoal, ThreadGoalStatus

LEGACY_GOAL_SCHEMA_VERSION = 1


class LegacyGoalDecodeError(ValueError):
    pass


def decode_legacy_goal(entries: list[dict[str, Any]]) -> ThreadGoal | None:
    """Fold v1 entries into the current ThreadGoal projection."""

    current: ThreadGoal | None = None
    current_revision: int | None = None
    for entry in entries:
        if entry.get("schemaVersion") != LEGACY_GOAL_SCHEMA_VERSION:
            raise LegacyGoalDecodeError("unsupported legacy Goal ledger schema")
        entry_type = entry.get("_type")
        try:
            if entry_type == "goal.snapshot":
                raw = _object(entry, "goal")
                goal, revision = _decode_snapshot(raw)
                if current is not None and current.id == goal.id:
                    if current_revision is not None and revision <= current_revision:
                        raise LegacyGoalDecodeError(
                            "legacy Goal revisions must increase monotonically"
                        )
                    if goal.created_at != current.created_at:
                        raise LegacyGoalDecodeError(
                            "legacy Goal created_at changed within one identity"
                        )
                    if goal.updated_at < current.updated_at:
                        raise LegacyGoalDecodeError(
                            "legacy Goal updated_at moved backwards"
                        )
                current = goal
                current_revision = revision
                continue
            if entry_type == "goal.cleared":
                if current is not None and entry.get("goalId") == current.id:
                    current = None
                    current_revision = None
                continue
            if entry_type in {
                "goal.attempt",
                "goal.evaluation",
                "goal.decision",
            }:
                payload_name = entry_type.removeprefix("goal.")
                payload = _object(entry, payload_name)
                if current is None or str(payload["goalId"]) != current.id:
                    raise LegacyGoalDecodeError(
                        f"legacy {payload_name} does not belong to the active Goal"
                    )
                continue
            raise LegacyGoalDecodeError(
                f"unknown legacy Goal ledger entry: {entry_type!r}"
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, LegacyGoalDecodeError):
                raise
            raise LegacyGoalDecodeError(
                f"invalid legacy Goal ledger entry: {entry_type!r}"
            ) from exc
    return current


def _decode_snapshot(raw: dict[str, Any]) -> tuple[ThreadGoal, int]:
    status = {
        "active": ThreadGoalStatus.ACTIVE,
        "paused": ThreadGoalStatus.PAUSED,
        "blocked": ThreadGoalStatus.BLOCKED,
        "usage_limited": ThreadGoalStatus.BLOCKED,
        "budget_limited": ThreadGoalStatus.BUDGET_LIMITED,
        "completed": ThreadGoalStatus.COMPLETE,
        "complete": ThreadGoalStatus.COMPLETE,
    }.get(str(raw["status"]))
    if status is None:
        raise LegacyGoalDecodeError(
            f"unknown legacy Goal status: {raw.get('status')!r}"
        )
    criteria = raw.get("acceptanceCriteria", [])
    if criteria is None:
        criteria = []
    if not isinstance(criteria, list) or not all(
        isinstance(value, str) and value.strip() for value in criteria
    ):
        raise LegacyGoalDecodeError(
            "legacy acceptanceCriteria must contain non-empty strings"
        )
    objective = str(raw["objective"]).strip()
    if criteria:
        objective = f"{objective}\n\nCompletion conditions:\n" + "\n".join(
            f"- {value.strip()}" for value in criteria
        )
    budget = raw.get("budget") or {}
    if not isinstance(budget, dict):
        raise LegacyGoalDecodeError("legacy Goal budget must be an object")
    revision = int(raw["revision"])
    if revision < 1:
        raise LegacyGoalDecodeError("legacy Goal revision must be positive")
    return (
        ThreadGoal(
            id=str(raw["id"]),
            thread_id=str(raw["threadId"]),
            objective=objective,
            status=status,
            token_budget=_optional_positive_int(budget.get("maxTokens")),
            tokens_used=_non_negative_int(raw.get("tokensUsed", 0)),
            time_used_seconds=_non_negative_int(raw.get("elapsedSeconds", 0)),
            skill_ids=tuple(raw.get("skillIds") or ()),
            created_at=datetime.fromisoformat(str(raw["createdAt"])),
            updated_at=datetime.fromisoformat(str(raw["updatedAt"])),
        ),
        revision,
    )


def _object(entry: dict[str, Any], key: str) -> dict[str, Any]:
    value = entry[key]
    if not isinstance(value, dict):
        raise LegacyGoalDecodeError(f"legacy {key} payload must be an object")
    return value


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise LegacyGoalDecodeError("legacy usage values must be integers")
    parsed = int(value)
    if parsed < 0:
        raise LegacyGoalDecodeError("legacy usage values cannot be negative")
    return parsed


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise LegacyGoalDecodeError("legacy token budget must be an integer")
    parsed = int(value)
    if parsed < 1:
        raise LegacyGoalDecodeError("legacy token budget must be positive")
    return parsed


__all__ = [
    "LEGACY_GOAL_SCHEMA_VERSION",
    "LegacyGoalDecodeError",
    "decode_legacy_goal",
]
