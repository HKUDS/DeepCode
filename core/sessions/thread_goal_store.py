"""Append-only persistence for the minimal Thread Goal model."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from core.domain.thread_goal import (
    GOAL_OUTCOME_REASON_MAX_CHARS,
    GoalDecisionSource,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)
from core.sessions.legacy_goal_decoder import (
    LegacyGoalDecodeError,
    decode_legacy_goal,
)
from core.sessions.store import SessionStore


GOAL_LEDGER_FILENAME = "goal.jsonl"
THREAD_GOAL_SCHEMA_VERSION = 2
THREAD_GOAL_SNAPSHOT = "thread_goal.snapshot"
THREAD_GOAL_CLEARED = "thread_goal.cleared"


class ThreadGoalStoreError(RuntimeError):
    """Base error for canonical Thread Goal persistence."""


class ThreadGoalSessionNotFoundError(ThreadGoalStoreError):
    pass


class ThreadGoalConflictError(ThreadGoalStoreError):
    pass


class ThreadGoalLedgerCorruptError(ThreadGoalStoreError):
    pass


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ThreadGoalRecord:
    goal: ThreadGoal | None
    outcome: GoalOutcome | None


class ThreadGoalStore:
    """Read legacy Goal ledgers and write only complete v2 transitions."""

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def read(self, thread_id: str) -> ThreadGoal | None:
        return self.read_record(thread_id).goal

    def read_record(self, thread_id: str) -> ThreadGoalRecord:
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            return self._fold_record(
                self._read_entries(directory),
                expected_thread_id=thread_id,
            )

    def read_guarded(self, thread_id: str, directory: Path) -> ThreadGoal | None:
        expected = self.sessions.root / self.sessions._validated_session_id(thread_id)
        if directory != expected:
            raise ValueError("guarded Goal directory does not match the Session")
        return self._fold_record(
            self._read_entries(directory),
            expected_thread_id=thread_id,
        ).goal

    def create(
        self,
        goal: ThreadGoal,
        *,
        reason: str = "created",
        source: str = "user",
    ) -> ThreadGoal:
        def mutation(current: ThreadGoal | None) -> tuple[dict[str, Any], ThreadGoal]:
            if current is not None:
                raise ThreadGoalConflictError(
                    f"session already has a Goal: {current.id}"
                )
            return self._snapshot_entry(goal, reason=reason, source=source), goal

        return self._mutate(goal.thread_id, mutation)

    def update(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        transform: Callable[[ThreadGoal], ThreadGoal],
        reason: str,
        source: str,
        turn_id: str | None = None,
    ) -> ThreadGoal:
        """Transform the latest Goal while holding the cross-process guard."""

        def mutation(current: ThreadGoal | None) -> tuple[dict[str, Any], ThreadGoal]:
            matched = self._matching_goal(current, expected_goal_id)
            goal = transform(matched)
            if goal.id != matched.id or goal.thread_id != matched.thread_id:
                raise ThreadGoalConflictError(
                    "Goal identity cannot change during update"
                )
            if goal == matched:
                return self._noop_entry(), matched
            if goal.created_at != matched.created_at:
                raise ThreadGoalConflictError("Goal created_at cannot change")
            if goal.updated_at < matched.updated_at:
                raise ThreadGoalConflictError("Goal updated_at cannot move backwards")
            return (
                self._snapshot_entry(
                    goal,
                    reason=reason,
                    source=source,
                    turn_id=turn_id,
                ),
                goal,
            )

        return self._mutate(thread_id, mutation)

    def clear(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        reason: str = "cleared",
        source: str = "user",
    ) -> bool:
        def mutation(current: ThreadGoal | None) -> tuple[dict[str, Any], bool]:
            if current is None:
                return self._noop_entry(), False
            self._matching_goal(current, expected_goal_id)
            return (
                {
                    "_type": THREAD_GOAL_CLEARED,
                    "schemaVersion": THREAD_GOAL_SCHEMA_VERSION,
                    "goalId": expected_goal_id,
                    "reason": self._clean_reason(reason),
                    "source": self._clean_source(source),
                },
                True,
            )

        return self._mutate(thread_id, mutation)

    def _mutate(
        self,
        thread_id: str,
        mutation: Callable[
            [ThreadGoal | None],
            tuple[dict[str, Any], _T],
        ],
    ) -> _T:
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            current = self._fold_record(
                self._read_entries(directory),
                expected_thread_id=thread_id,
            ).goal
            entry, result = mutation(current)
            if entry.get("_type") != "thread_goal.noop":
                self._append_entry(directory, entry)
            return result

    @staticmethod
    def _matching_goal(
        current: ThreadGoal | None,
        expected_goal_id: str,
    ) -> ThreadGoal:
        if current is None or current.id != expected_goal_id:
            raise ThreadGoalConflictError(
                "Goal no longer exists or has a different identity"
            )
        return current

    @staticmethod
    def _noop_entry() -> dict[str, Any]:
        return {"_type": "thread_goal.noop"}

    @classmethod
    def _fold(
        cls,
        entries: list[dict[str, Any]],
        *,
        expected_thread_id: str,
    ) -> ThreadGoal | None:
        return cls._fold_record(
            entries,
            expected_thread_id=expected_thread_id,
        ).goal

    @classmethod
    def _fold_record(
        cls,
        entries: list[dict[str, Any]],
        *,
        expected_thread_id: str,
    ) -> ThreadGoalRecord:
        legacy: list[dict[str, Any]] = []
        current: ThreadGoal | None = None
        outcome: GoalOutcome | None = None
        saw_v2 = False
        for entry in entries:
            schema_version = entry.get("schemaVersion")
            if schema_version == 1:
                if saw_v2:
                    raise ThreadGoalLedgerCorruptError(
                        "legacy Goal entries cannot follow a v2 transition"
                    )
                legacy.append(entry)
                continue
            if schema_version != THREAD_GOAL_SCHEMA_VERSION:
                raise ThreadGoalLedgerCorruptError(
                    f"unsupported Goal ledger schema: {schema_version!r}"
                )
            if not saw_v2:
                current = cls._legacy_current(legacy)
                outcome = None
                saw_v2 = True
            entry_type = entry.get("_type")
            try:
                if entry_type == THREAD_GOAL_SNAPSHOT:
                    goal = cls._goal_from_dict(entry["goal"])
                    if goal.thread_id != expected_thread_id:
                        raise ThreadGoalLedgerCorruptError(
                            "Goal snapshot belongs to another Session"
                        )
                    if current is not None and goal.id != current.id:
                        raise ThreadGoalLedgerCorruptError(
                            "a new Goal requires a preceding clear transition"
                        )
                    if current is not None and goal.created_at != current.created_at:
                        raise ThreadGoalLedgerCorruptError(
                            "Goal created_at changed within one Goal identity"
                        )
                    if current is not None and goal.updated_at < current.updated_at:
                        raise ThreadGoalLedgerCorruptError(
                            "Goal updated_at moved backwards"
                        )
                    previous_status = current.status if current is not None else None
                    if (
                        goal.status
                        in {ThreadGoalStatus.COMPLETE, ThreadGoalStatus.BLOCKED}
                        and goal.status is not previous_status
                    ):
                        outcome = cls._outcome_from_entry(entry, goal)
                    elif goal.status is ThreadGoalStatus.ACTIVE:
                        outcome = None
                    current = goal
                elif entry_type == THREAD_GOAL_CLEARED:
                    if current is None:
                        continue
                    if entry.get("goalId") != current.id:
                        raise ThreadGoalLedgerCorruptError(
                            "Goal clear transition targets another Goal"
                        )
                    current = None
                    outcome = None
                else:
                    raise ThreadGoalLedgerCorruptError(
                        f"unknown Goal ledger entry: {entry_type!r}"
                    )
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ThreadGoalLedgerCorruptError):
                    raise
                raise ThreadGoalLedgerCorruptError(
                    f"invalid Goal ledger entry: {entry_type!r}"
                ) from exc
        if not saw_v2:
            current = cls._legacy_current(legacy)
            outcome = None
        if current is not None and current.thread_id != expected_thread_id:
            raise ThreadGoalLedgerCorruptError(
                "Goal snapshot belongs to another Session"
            )
        return ThreadGoalRecord(goal=current, outcome=outcome)

    @classmethod
    def _outcome_from_entry(
        cls,
        entry: dict[str, Any],
        goal: ThreadGoal,
    ) -> GoalOutcome:
        reason = cls._clean_reason(str(entry["reason"]))
        source = GoalDecisionSource(cls._clean_source(str(entry["source"])))
        turn_id = entry.get("turnId")
        if turn_id is not None and (not isinstance(turn_id, str) or not turn_id):
            raise ThreadGoalLedgerCorruptError(
                "Goal outcome turnId must be a non-empty string"
            )
        return GoalOutcome(
            status=goal.status,
            reason=reason,
            source=source,
            decided_by_turn_id=turn_id,
            decided_at=goal.updated_at,
        )

    @classmethod
    def _legacy_current(cls, entries: list[dict[str, Any]]) -> ThreadGoal | None:
        if not entries:
            return None
        try:
            return decode_legacy_goal(entries)
        except LegacyGoalDecodeError as exc:
            raise ThreadGoalLedgerCorruptError(str(exc)) from exc

    @classmethod
    def _snapshot_entry(
        cls,
        goal: ThreadGoal,
        *,
        reason: str,
        source: str,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "_type": THREAD_GOAL_SNAPSHOT,
            "schemaVersion": THREAD_GOAL_SCHEMA_VERSION,
            "goal": cls._goal_to_dict(goal),
            "reason": cls._clean_reason(reason),
            "source": cls._clean_source(source),
            **({"turnId": turn_id} if turn_id is not None else {}),
        }

    @staticmethod
    def _goal_to_dict(goal: ThreadGoal) -> dict[str, Any]:
        return {
            "id": goal.id,
            "threadId": goal.thread_id,
            "objective": goal.objective,
            "status": goal.status.value,
            "tokenBudget": goal.token_budget,
            "tokensUsed": goal.tokens_used,
            "timeUsedSeconds": goal.time_used_seconds,
            "skillIds": list(goal.skill_ids),
            "createdAt": goal.created_at.isoformat(),
            "updatedAt": goal.updated_at.isoformat(),
        }

    @staticmethod
    def _goal_from_dict(raw: dict[str, Any]) -> ThreadGoal:
        return ThreadGoal(
            id=str(raw["id"]),
            thread_id=str(raw["threadId"]),
            objective=str(raw["objective"]),
            status=ThreadGoalStatus(raw["status"]),
            token_budget=raw.get("tokenBudget"),
            tokens_used=int(raw.get("tokensUsed", 0)),
            time_used_seconds=int(raw.get("timeUsedSeconds", 0)),
            skill_ids=tuple(raw.get("skillIds") or ()),
            created_at=datetime.fromisoformat(raw["createdAt"]),
            updated_at=datetime.fromisoformat(raw["updatedAt"]),
        )

    @staticmethod
    def _clean_reason(value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Goal transition reason must not be empty")
        if len(clean) > GOAL_OUTCOME_REASON_MAX_CHARS:
            raise ValueError(
                "Goal transition reason may contain at most "
                f"{GOAL_OUTCOME_REASON_MAX_CHARS} characters"
            )
        return clean

    @staticmethod
    def _clean_source(value: str) -> str:
        clean = value.strip()
        if clean not in {"user", "agent", "runtime", "migration"}:
            raise ValueError(f"unsupported Goal transition source: {clean!r}")
        return clean

    @staticmethod
    def _ledger_path(directory: Path) -> Path:
        return directory / GOAL_LEDGER_FILENAME

    @classmethod
    def _read_entries(cls, directory: Path) -> list[dict[str, Any]]:
        path = cls._ledger_path(directory)
        if not path.exists():
            return []
        raw = path.read_bytes()
        committed_end = raw.rfind(b"\n")
        if committed_end < 0:
            return []
        entries: list[dict[str, Any]] = []
        try:
            committed = raw[: committed_end + 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ThreadGoalLedgerCorruptError(
                "Goal ledger contains invalid UTF-8 in a committed record"
            ) from exc
        for line_number, line in enumerate(committed.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ThreadGoalLedgerCorruptError(
                    f"Goal ledger contains invalid JSON at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise ThreadGoalLedgerCorruptError(
                    f"Goal ledger line {line_number} is not an object"
                )
            entries.append(value)
        return entries

    @classmethod
    def _append_entry(cls, directory: Path, entry: dict[str, Any]) -> None:
        path = cls._ledger_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._discard_uncommitted_tail(path)
        encoded = (
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("Goal ledger write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _discard_uncommitted_tail(path: Path) -> None:
        """Remove only bytes after the last committed JSONL boundary."""

        if not path.exists():
            return
        with path.open("r+b") as handle:
            raw = handle.read()
            if not raw or raw.endswith(b"\n"):
                return
            committed_end = raw.rfind(b"\n")
            handle.truncate(committed_end + 1 if committed_end >= 0 else 0)
            handle.flush()
            os.fsync(handle.fileno())


__all__ = [
    "THREAD_GOAL_CLEARED",
    "THREAD_GOAL_SCHEMA_VERSION",
    "THREAD_GOAL_SNAPSHOT",
    "ThreadGoalConflictError",
    "ThreadGoalLedgerCorruptError",
    "ThreadGoalRecord",
    "ThreadGoalSessionNotFoundError",
    "ThreadGoalStore",
    "ThreadGoalStoreError",
]
