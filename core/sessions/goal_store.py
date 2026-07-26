"""Canonical append-only Goal ledger stored beside a Session transcript."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from core.domain.goal import (
    DEFAULT_GOAL_POLICY,
    Goal,
    GoalAttempt,
    GoalAttemptStatus,
    GoalBudget,
    GoalEvaluation,
    GoalRecord,
    GoalStatus,
    GoalVerdict,
)
from core.sessions.store import SessionStore


GOAL_LEDGER_FILENAME = "goal.jsonl"
GOAL_LEDGER_SCHEMA_VERSION = 1


class GoalStoreError(RuntimeError):
    """Base error for canonical Goal persistence."""


class GoalSessionNotFoundError(GoalStoreError):
    pass


class GoalConflictError(GoalStoreError):
    pass


class GoalLedgerCorruptError(GoalStoreError):
    pass


_T = TypeVar("_T")


class GoalStore:
    """Read and mutate one active Goal per canonical Session.

    Every mutation re-reads the ledger while holding the Session's existing
    cross-process lock. Revisions therefore provide compare-and-swap semantics
    across CLI and Desktop processes.
    """

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def read(self, thread_id: str) -> GoalRecord | None:
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise GoalSessionNotFoundError(f"session not found: {thread_id}")
            return self._fold(self._read_entries(directory))

    def read_guarded(self, thread_id: str, directory: Path) -> GoalRecord | None:
        """Read while the caller already owns the Session mutation guard.

        Permanent deletion uses this narrow seam to inspect Goal state without
        recursively acquiring the same cross-process file lock.
        """

        expected = self.sessions.root / self.sessions._validated_session_id(thread_id)
        if directory != expected:
            raise ValueError("guarded Goal directory does not match the Session")
        return self._fold(self._read_entries(directory))

    def create(self, goal: Goal) -> GoalRecord:
        if goal.revision != 1:
            raise ValueError("new goals must start at revision 1")

        def mutation(record: GoalRecord | None) -> tuple[dict[str, Any], GoalRecord]:
            if record is not None and not record.goal.status.is_terminal:
                raise GoalConflictError(
                    f"session already has an unfinished goal: {record.goal.id}"
                )
            return self._goal_entry(goal), GoalRecord(goal)

        return self._mutate(goal.thread_id, mutation)

    def update(
        self,
        goal: Goal,
        *,
        expected_revision: int,
    ) -> GoalRecord:
        if goal.revision != expected_revision + 1:
            raise ValueError("updated goal revision must increment exactly once")

        def mutation(record: GoalRecord | None) -> tuple[dict[str, Any], GoalRecord]:
            current = self._matching_goal(record, goal.id)
            self._require_revision(current, expected_revision)
            return self._goal_entry(goal), GoalRecord(
                goal,
                attempts=current.attempts,
                evaluations=current.evaluations,
            )

        return self._mutate(goal.thread_id, mutation)

    def append_attempt(
        self,
        thread_id: str,
        attempt: GoalAttempt,
        *,
        expected_revision: int,
    ) -> GoalRecord:
        def mutation(record: GoalRecord | None) -> tuple[dict[str, Any], GoalRecord]:
            current = self._matching_goal(record, attempt.goal_id)
            self._require_revision(current, expected_revision)
            existing = next(
                (item for item in current.attempts if item.id == attempt.id),
                None,
            )
            if existing is not None:
                if existing == attempt:
                    return self._noop_entry(), current
                if (
                    existing.goal_id != attempt.goal_id
                    or existing.goal_revision != attempt.goal_revision
                    or existing.ordinal != attempt.ordinal
                ):
                    raise GoalConflictError(
                        "Goal Attempt identity fields cannot be changed"
                    )
                attempts = tuple(
                    attempt if item.id == attempt.id else item
                    for item in current.attempts
                )
                return self._attempt_entry(attempt), GoalRecord(
                    current.goal,
                    attempts=attempts,
                    evaluations=current.evaluations,
                )
            if any(
                existing.ordinal == attempt.ordinal for existing in current.attempts
            ):
                raise GoalConflictError(
                    f"goal attempt ordinal already exists: {attempt.ordinal}"
                )
            if attempt.goal_revision != current.goal.definition_revision:
                raise GoalConflictError("attempt belongs to a stale Goal definition")
            return self._attempt_entry(attempt), GoalRecord(
                current.goal,
                attempts=(*current.attempts, attempt),
                evaluations=current.evaluations,
            )

        return self._mutate(thread_id, mutation)

    def append_evaluation(
        self,
        thread_id: str,
        evaluation: GoalEvaluation,
        *,
        expected_revision: int,
    ) -> GoalRecord:
        def mutation(record: GoalRecord | None) -> tuple[dict[str, Any], GoalRecord]:
            current = self._matching_goal(record, evaluation.goal_id)
            self._require_revision(current, expected_revision)
            if evaluation.goal_revision != current.goal.definition_revision:
                raise GoalConflictError("evaluation belongs to a stale Goal definition")
            if not any(
                attempt.id == evaluation.attempt_id
                and attempt.turn_id == evaluation.turn_id
                for attempt in current.attempts
            ):
                raise GoalConflictError(
                    "evaluation must reference a persisted Goal Attempt and Turn"
                )
            duplicate = next(
                (
                    existing
                    for existing in current.evaluations
                    if existing.goal_revision == evaluation.goal_revision
                    and existing.turn_id == evaluation.turn_id
                ),
                None,
            )
            if duplicate is not None:
                if duplicate == evaluation:
                    return self._noop_entry(), current
                raise GoalConflictError(
                    "this Turn already has an evaluation for the Goal revision"
                )
            return self._evaluation_entry(evaluation), GoalRecord(
                current.goal,
                attempts=current.attempts,
                evaluations=(*current.evaluations, evaluation),
            )

        return self._mutate(thread_id, mutation)

    def commit_evaluation(
        self,
        thread_id: str,
        evaluation: GoalEvaluation,
        updated_goal: Goal,
        *,
        expected_revision: int,
    ) -> GoalRecord:
        """Persist one evaluation and its resulting Goal state atomically."""

        if updated_goal.revision != expected_revision + 1:
            raise ValueError("evaluation must increment the Goal revision once")

        def mutation(
            record: GoalRecord | None,
        ) -> tuple[tuple[dict[str, Any], ...], GoalRecord]:
            current = self._matching_goal(record, evaluation.goal_id)
            self._require_revision(current, expected_revision)
            if updated_goal.id != current.goal.id:
                raise GoalConflictError("updated Goal identity does not match")
            if evaluation.goal_revision != current.goal.definition_revision:
                raise GoalConflictError("evaluation belongs to a stale Goal definition")
            if updated_goal.definition_revision != current.goal.definition_revision:
                raise GoalConflictError(
                    "an evaluation cannot change the Goal definition"
                )
            if not any(
                attempt.id == evaluation.attempt_id
                and attempt.turn_id == evaluation.turn_id
                for attempt in current.attempts
            ):
                raise GoalConflictError(
                    "evaluation must reference a persisted Goal Attempt and Turn"
                )
            if any(
                existing.goal_revision == evaluation.goal_revision
                and existing.turn_id == evaluation.turn_id
                for existing in current.evaluations
            ):
                raise GoalConflictError(
                    "this Turn already has an evaluation for the Goal definition"
                )
            next_record = GoalRecord(
                updated_goal,
                attempts=current.attempts,
                evaluations=(*current.evaluations, evaluation),
            )
            return (
                (
                    self._evaluation_entry(evaluation),
                    self._goal_entry(updated_goal),
                ),
                next_record,
            )

        return self._mutate(thread_id, mutation)

    def clear(
        self,
        thread_id: str,
        *,
        goal_id: str,
        expected_revision: int,
    ) -> None:
        def mutation(record: GoalRecord | None) -> tuple[dict[str, Any], None]:
            current = self._matching_goal(record, goal_id)
            self._require_revision(current, expected_revision)
            return (
                {
                    "_type": "goal.cleared",
                    "schemaVersion": GOAL_LEDGER_SCHEMA_VERSION,
                    "goalId": goal_id,
                    "expectedRevision": expected_revision,
                },
                None,
            )

        self._mutate(thread_id, mutation)

    def _mutate(
        self,
        thread_id: str,
        mutation: Callable[
            [GoalRecord | None],
            tuple[dict[str, Any] | tuple[dict[str, Any], ...], _T],
        ],
    ) -> _T:
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise GoalSessionNotFoundError(f"session not found: {thread_id}")
            record = self._fold(self._read_entries(directory))
            entries, result = mutation(record)
            pending = entries if isinstance(entries, tuple) else (entries,)
            for entry in pending:
                if entry.get("_type") != "goal.noop":
                    self._append_entry(directory, entry)
            return result

    @staticmethod
    def _matching_goal(record: GoalRecord | None, goal_id: str) -> GoalRecord:
        if record is None or record.goal.id != goal_id:
            raise GoalConflictError("goal no longer exists or has been replaced")
        return record

    @staticmethod
    def _require_revision(record: GoalRecord, expected_revision: int) -> None:
        if record.goal.revision != expected_revision:
            raise GoalConflictError(
                "goal revision changed; reload before applying the mutation"
            )

    @staticmethod
    def _noop_entry() -> dict[str, Any]:
        return {"_type": "goal.noop"}

    @classmethod
    def _fold(cls, entries: list[dict[str, Any]]) -> GoalRecord | None:
        record: GoalRecord | None = None
        for entry in entries:
            entry_type = entry.get("_type")
            if entry.get("schemaVersion") != GOAL_LEDGER_SCHEMA_VERSION:
                raise GoalLedgerCorruptError("unsupported Goal ledger schema")
            try:
                if entry_type == "goal.snapshot":
                    goal = cls._goal_from_dict(entry["goal"])
                    if record is None or record.goal.id != goal.id:
                        record = GoalRecord(goal)
                    else:
                        if goal.revision <= record.goal.revision:
                            raise GoalLedgerCorruptError(
                                "Goal revisions must increase monotonically"
                            )
                        record = GoalRecord(
                            goal,
                            attempts=record.attempts,
                            evaluations=record.evaluations,
                        )
                elif entry_type == "goal.attempt":
                    attempt = cls._attempt_from_dict(entry["attempt"])
                    if record is None or attempt.goal_id != record.goal.id:
                        raise GoalLedgerCorruptError(
                            "Goal Attempt does not belong to the active Goal"
                        )
                    existing = next(
                        (item for item in record.attempts if item.id == attempt.id),
                        None,
                    )
                    attempts = (
                        tuple(
                            attempt if item.id == attempt.id else item
                            for item in record.attempts
                        )
                        if existing is not None
                        else (*record.attempts, attempt)
                    )
                    record = GoalRecord(
                        record.goal,
                        attempts=attempts,
                        evaluations=record.evaluations,
                    )
                elif entry_type == "goal.evaluation":
                    evaluation = cls._evaluation_from_dict(entry["evaluation"])
                    if record is None or evaluation.goal_id != record.goal.id:
                        raise GoalLedgerCorruptError(
                            "Goal Evaluation does not belong to the active Goal"
                        )
                    record = GoalRecord(
                        record.goal,
                        attempts=record.attempts,
                        evaluations=(*record.evaluations, evaluation),
                    )
                elif entry_type == "goal.cleared":
                    if record is not None and entry.get("goalId") == record.goal.id:
                        record = None
                else:
                    raise GoalLedgerCorruptError(
                        f"unknown Goal ledger entry: {entry_type!r}"
                    )
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, GoalLedgerCorruptError):
                    raise
                raise GoalLedgerCorruptError(
                    f"invalid Goal ledger entry: {entry_type!r}"
                ) from exc
        return record

    @staticmethod
    def _ledger_path(directory: Path) -> Path:
        return directory / GOAL_LEDGER_FILENAME

    @classmethod
    def _read_entries(cls, directory: Path) -> list[dict[str, Any]]:
        path = cls._ledger_path(directory)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    if not raw.strip():
                        continue
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise GoalLedgerCorruptError(
                            f"Goal ledger line {line_number} is not an object"
                        )
                    entries.append(value)
        except json.JSONDecodeError as exc:
            raise GoalLedgerCorruptError(
                f"Goal ledger contains invalid JSON at line {exc.lineno}"
            ) from exc
        return entries

    @classmethod
    def _append_entry(cls, directory: Path, entry: dict[str, Any]) -> None:
        path = cls._ledger_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @classmethod
    def _goal_entry(cls, goal: Goal) -> dict[str, Any]:
        return {
            "_type": "goal.snapshot",
            "schemaVersion": GOAL_LEDGER_SCHEMA_VERSION,
            "goal": cls._goal_to_dict(goal),
        }

    @classmethod
    def _attempt_entry(cls, attempt: GoalAttempt) -> dict[str, Any]:
        return {
            "_type": "goal.attempt",
            "schemaVersion": GOAL_LEDGER_SCHEMA_VERSION,
            "attempt": cls._attempt_to_dict(attempt),
        }

    @classmethod
    def _evaluation_entry(cls, evaluation: GoalEvaluation) -> dict[str, Any]:
        return {
            "_type": "goal.evaluation",
            "schemaVersion": GOAL_LEDGER_SCHEMA_VERSION,
            "evaluation": cls._evaluation_to_dict(evaluation),
        }

    @staticmethod
    def _goal_to_dict(goal: Goal) -> dict[str, Any]:
        return {
            "id": goal.id,
            "threadId": goal.thread_id,
            "objective": goal.objective,
            "acceptanceCriteria": list(goal.acceptance_criteria),
            "status": goal.status.value,
            "revision": goal.revision,
            "definitionRevision": goal.definition_revision,
            "tokensUsed": goal.tokens_used,
            "elapsedSeconds": goal.elapsed_seconds,
            "budget": {
                "maxAttempts": goal.budget.max_attempts,
                "maxTokens": goal.budget.max_tokens,
                "maxElapsedSeconds": goal.budget.max_elapsed_seconds,
            },
            "skillIds": list(goal.skill_ids),
            "verificationCommandId": goal.verification_command_id,
            "verificationTimeoutSeconds": goal.verification_timeout_seconds,
            "evaluatorConnectionId": goal.evaluator_connection_id,
            "evaluatorModelId": goal.evaluator_model_id,
            "evaluatorMaxTokens": goal.evaluator_max_tokens,
            "evaluatorTemperature": goal.evaluator_temperature,
            "evaluatorRetryLimit": goal.evaluator_retry_limit,
            "stallThreshold": goal.stall_threshold,
            "lastVerdict": goal.last_verdict.value if goal.last_verdict else None,
            "lastReason": goal.last_reason,
            "createdAt": goal.created_at.isoformat(),
            "updatedAt": goal.updated_at.isoformat(),
            "activeSince": (
                goal.active_since.isoformat() if goal.active_since else None
            ),
            "completedAt": (
                goal.completed_at.isoformat() if goal.completed_at else None
            ),
        }

    @classmethod
    def _goal_from_dict(cls, raw: dict[str, Any]) -> Goal:
        budget = raw.get("budget") or {}
        return Goal(
            id=str(raw["id"]),
            thread_id=str(raw["threadId"]),
            objective=str(raw["objective"]),
            acceptance_criteria=tuple(raw.get("acceptanceCriteria") or ()),
            status=GoalStatus(raw["status"]),
            revision=int(raw["revision"]),
            definition_revision=int(raw.get("definitionRevision", raw["revision"])),
            tokens_used=int(raw.get("tokensUsed", 0)),
            elapsed_seconds=int(raw.get("elapsedSeconds", 0)),
            budget=GoalBudget(
                max_attempts=budget.get("maxAttempts"),
                max_tokens=budget.get("maxTokens"),
                max_elapsed_seconds=budget.get("maxElapsedSeconds"),
            ),
            skill_ids=tuple(raw.get("skillIds") or ()),
            verification_command_id=raw.get("verificationCommandId"),
            verification_timeout_seconds=int(
                raw.get(
                    "verificationTimeoutSeconds",
                    DEFAULT_GOAL_POLICY.verification_timeout_seconds,
                )
            ),
            evaluator_connection_id=raw.get("evaluatorConnectionId"),
            evaluator_model_id=raw.get("evaluatorModelId"),
            evaluator_max_tokens=int(
                raw.get(
                    "evaluatorMaxTokens",
                    DEFAULT_GOAL_POLICY.evaluator_max_tokens,
                )
            ),
            evaluator_temperature=float(
                raw.get(
                    "evaluatorTemperature",
                    DEFAULT_GOAL_POLICY.evaluator_temperature,
                )
            ),
            evaluator_retry_limit=int(
                raw.get(
                    "evaluatorRetryLimit",
                    DEFAULT_GOAL_POLICY.evaluator_retry_limit,
                )
            ),
            stall_threshold=int(
                raw.get(
                    "stallThreshold",
                    DEFAULT_GOAL_POLICY.stall_threshold,
                )
            ),
            last_verdict=(
                GoalVerdict(raw["lastVerdict"])
                if raw.get("lastVerdict") is not None
                else None
            ),
            last_reason=raw.get("lastReason"),
            created_at=datetime.fromisoformat(raw["createdAt"]),
            updated_at=datetime.fromisoformat(raw["updatedAt"]),
            active_since=(
                datetime.fromisoformat(raw["activeSince"])
                if raw.get("activeSince")
                else None
            ),
            completed_at=(
                datetime.fromisoformat(raw["completedAt"])
                if raw.get("completedAt")
                else None
            ),
        )

    @staticmethod
    def _attempt_to_dict(attempt: GoalAttempt) -> dict[str, Any]:
        return {
            "id": attempt.id,
            "goalId": attempt.goal_id,
            "goalRevision": attempt.goal_revision,
            "ordinal": attempt.ordinal,
            "status": attempt.status.value,
            "turnId": attempt.turn_id,
            "createdAt": attempt.created_at.isoformat(),
            "updatedAt": attempt.updated_at.isoformat(),
            "completedAt": (
                attempt.completed_at.isoformat() if attempt.completed_at else None
            ),
        }

    @staticmethod
    def _attempt_from_dict(raw: dict[str, Any]) -> GoalAttempt:
        return GoalAttempt(
            id=str(raw["id"]),
            goal_id=str(raw["goalId"]),
            goal_revision=int(raw["goalRevision"]),
            ordinal=int(raw["ordinal"]),
            status=GoalAttemptStatus(raw["status"]),
            turn_id=raw.get("turnId"),
            created_at=datetime.fromisoformat(raw["createdAt"]),
            updated_at=datetime.fromisoformat(raw["updatedAt"]),
            completed_at=(
                datetime.fromisoformat(raw["completedAt"])
                if raw.get("completedAt")
                else None
            ),
        )

    @staticmethod
    def _evaluation_to_dict(evaluation: GoalEvaluation) -> dict[str, Any]:
        return {
            "id": evaluation.id,
            "goalId": evaluation.goal_id,
            "goalRevision": evaluation.goal_revision,
            "attemptId": evaluation.attempt_id,
            "turnId": evaluation.turn_id,
            "verdict": evaluation.verdict.value,
            "reason": evaluation.reason,
            "evidenceRefs": list(evaluation.evidence_refs),
            "evaluatorProvider": evaluation.evaluator_provider,
            "evaluatorModel": evaluation.evaluator_model,
            "tokensUsed": evaluation.tokens_used,
            "createdAt": evaluation.created_at.isoformat(),
        }

    @staticmethod
    def _evaluation_from_dict(raw: dict[str, Any]) -> GoalEvaluation:
        return GoalEvaluation(
            id=str(raw["id"]),
            goal_id=str(raw["goalId"]),
            goal_revision=int(raw["goalRevision"]),
            attempt_id=str(raw["attemptId"]),
            turn_id=str(raw["turnId"]),
            verdict=GoalVerdict(raw["verdict"]),
            reason=str(raw["reason"]),
            evidence_refs=tuple(raw.get("evidenceRefs") or ()),
            evaluator_provider=raw.get("evaluatorProvider"),
            evaluator_model=raw.get("evaluatorModel"),
            tokens_used=int(raw.get("tokensUsed", 0)),
            created_at=datetime.fromisoformat(raw["createdAt"]),
        )


__all__ = [
    "GOAL_LEDGER_FILENAME",
    "GOAL_LEDGER_SCHEMA_VERSION",
    "GoalConflictError",
    "GoalLedgerCorruptError",
    "GoalSessionNotFoundError",
    "GoalStore",
    "GoalStoreError",
]
