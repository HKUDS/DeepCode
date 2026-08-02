"""Append-only persistence for the minimal Thread Goal model."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from core.domain.thread_goal import (
    GOAL_OUTCOME_REASON_MAX_CHARS,
    GoalDecisionSource,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)
from core.private_storage import open_private_file
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


class ThreadGoalIdentityRetiredError(ThreadGoalConflictError):
    """A cleared Goal identity cannot be provisioned again."""


class ThreadGoalLedgerCorruptError(ThreadGoalStoreError):
    pass


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ThreadGoalRecord:
    goal: ThreadGoal | None
    outcome: GoalOutcome | None
    seen_goal_ids: frozenset[str] = frozenset()
    turn_settlement_ids: frozenset[str] = frozenset()


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

    @contextmanager
    def guarded_record(self, thread_id: str) -> Iterator[ThreadGoalRecord]:
        """Hold the canonical cross-process Session lock while a caller acts."""

        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            yield self._fold_record(
                self._read_entries(directory),
                expected_thread_id=thread_id,
            )

    def has_turn_settlement(
        self,
        thread_id: str,
        *,
        goal_id: str,
        turn_id: str,
    ) -> bool:
        """Return whether runtime accounting for one Goal Turn is durable."""

        return self.has_turn_settlements(
            thread_id,
            goal_id=goal_id,
            turn_ids=(turn_id,),
        )

    def has_turn_settlements(
        self,
        thread_id: str,
        *,
        goal_id: str,
        turn_ids: tuple[str, ...],
    ) -> bool:
        """Check a bounded Turn set with one canonical ledger read."""

        if not turn_ids:
            return True
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            entries = self._read_entries(directory)
        settled = self._turn_settlement_ids(
            entries,
            goal_id=goal_id,
        )
        return all(turn_id in settled for turn_id in turn_ids)

    def list_current(self) -> tuple[ThreadGoal, ...]:
        """Read current Goals from canonical Session-owned ledgers."""

        root = self.sessions.root
        if not root.exists():
            return ()
        session_ids = sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir()
            and (entry / "session.jsonl").is_file()
            and (entry / "goal.jsonl").is_file()
        )
        goals: list[ThreadGoal] = []
        for session_id in session_ids:
            try:
                goal = self.read(session_id)
            except ThreadGoalSessionNotFoundError:
                continue
            if goal is not None:
                goals.append(goal)
        return tuple(goals)

    def settle_turn(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        turn_id: str,
        transform: Callable[[ThreadGoal], ThreadGoal],
        reason: str,
    ) -> tuple[ThreadGoal, bool]:
        """Account one runtime Turn exactly once across recovery retries."""

        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            entries = self._read_entries(directory)
            current = self._fold_record(
                entries,
                expected_thread_id=thread_id,
            ).goal
            matched = self._matching_goal(current, expected_goal_id)
            if self._has_turn_settlement_entry(
                entries,
                goal_id=matched.id,
                turn_id=turn_id,
            ):
                return matched, False
            entry, updated = self._updated_snapshot(
                matched,
                expected_goal_id=expected_goal_id,
                transform=transform,
                reason=reason,
                source="runtime",
                turn_id=turn_id,
            )
            if entry.get("_type") != "thread_goal.noop":
                self._append_entry(directory, entry)
            return updated, True

    @staticmethod
    def _has_turn_settlement_entry(
        entries: list[dict[str, Any]],
        *,
        goal_id: str,
        turn_id: str,
    ) -> bool:
        return turn_id in ThreadGoalStore._turn_settlement_ids(
            entries,
            goal_id=goal_id,
        )

    @staticmethod
    def _turn_settlement_ids(
        entries: list[dict[str, Any]],
        *,
        goal_id: str,
    ) -> frozenset[str]:
        return frozenset(
            str(entry["turnId"])
            for entry in entries
            if (
                entry.get("_type") == THREAD_GOAL_SNAPSHOT
                and entry.get("schemaVersion") == THREAD_GOAL_SCHEMA_VERSION
                and entry.get("source") == GoalDecisionSource.RUNTIME.value
                and isinstance(entry.get("turnId"), str)
                and isinstance(entry.get("goal"), dict)
                and entry["goal"].get("id") == goal_id
            )
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
        def mutation(record: ThreadGoalRecord) -> tuple[dict[str, Any], ThreadGoal]:
            current = record.goal
            if current is not None:
                raise ThreadGoalConflictError(
                    f"session already has a Goal: {current.id}"
                )
            self._ensure_fresh_identity(record, goal.id)
            return self._snapshot_entry(goal, reason=reason, source=source), goal

        return self._mutate(goal.thread_id, mutation)

    def provision(
        self,
        goal: ThreadGoal,
        *,
        reason: str = "provisioned",
        source: str = "user",
    ) -> tuple[ThreadGoal, bool]:
        """Create a caller-owned Goal identity or return its durable retry.

        The comparison intentionally covers only the declarative provisioning
        content. Status and usage may advance after provisioning and must not
        turn a recovery retry into a conflict.
        """

        def mutation(
            record: ThreadGoalRecord,
        ) -> tuple[dict[str, Any], tuple[ThreadGoal, bool]]:
            current = record.goal
            if current is None:
                self._ensure_fresh_identity(record, goal.id)
                return (
                    self._snapshot_entry(goal, reason=reason, source=source),
                    (goal, True),
                )
            if current.id != goal.id:
                raise ThreadGoalConflictError(
                    "session already has a different Goal: "
                    f"requested {goal.id}, actual {current.id}"
                )
            if (
                current.objective != goal.objective
                or current.token_budget != goal.token_budget
                or current.skill_ids != goal.skill_ids
            ):
                raise ThreadGoalConflictError(
                    f"Goal provisioning content changed for {goal.id}"
                )
            return self._noop_entry(), (current, False)

        return self._mutate(goal.thread_id, mutation)

    def provision_replacing_completed(
        self,
        goal: ThreadGoal,
        *,
        expected_current_goal_id: str,
        reason: str = "provisioned",
        source: str = "user",
    ) -> ThreadGoal:
        """Replace one exact completed Goal while holding the Session lock.

        Identity freshness is checked before the old Goal is cleared. The two
        append-only transitions are recovery-safe: if a process stops after the
        clear record, a retry may still provision the fresh requested identity.
        """

        with self.sessions.session_guard(goal.thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(
                    f"session not found: {goal.thread_id}"
                )
            record = self._fold_record(
                self._read_entries(directory),
                expected_thread_id=goal.thread_id,
            )
            current = self._matching_goal(
                record.goal,
                expected_current_goal_id,
            )
            if current.status is not ThreadGoalStatus.COMPLETE:
                raise ThreadGoalConflictError(
                    f"Goal {current.id} is not complete and cannot be replaced"
                )
            self._ensure_fresh_identity(record, goal.id)
            self._append_entry(
                directory,
                {
                    "_type": THREAD_GOAL_CLEARED,
                    "schemaVersion": THREAD_GOAL_SCHEMA_VERSION,
                    "goalId": current.id,
                    "reason": "replaced by caller-owned Goal",
                    "source": self._clean_source(source),
                },
            )
            self._append_entry(
                directory,
                self._snapshot_entry(goal, reason=reason, source=source),
            )
            return goal

    def update(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        transform: Callable[[ThreadGoal], ThreadGoal],
        reason: str,
        source: str,
        turn_id: str | None = None,
        guard: Callable[[ThreadGoalRecord], None] | None = None,
    ) -> ThreadGoal:
        """Transform the latest Goal while holding its cross-process guard.

        ``guard`` runs against the same record immediately before append. It
        must not re-enter this Goal store. Callers that inspect SQLite must
        preserve the global Session-lock-before-SQLite lock order.
        """

        def mutation(record: ThreadGoalRecord) -> tuple[dict[str, Any], ThreadGoal]:
            self._matching_goal(record.goal, expected_goal_id)
            if guard is not None:
                guard(record)
            return self._updated_snapshot(
                record.goal,
                expected_goal_id=expected_goal_id,
                transform=transform,
                reason=reason,
                source=source,
                turn_id=turn_id,
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
        def mutation(record: ThreadGoalRecord) -> tuple[dict[str, Any], bool]:
            current = record.goal
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
            [ThreadGoalRecord],
            tuple[dict[str, Any], _T],
        ],
    ) -> _T:
        with self.sessions.session_guard(thread_id) as directory:
            if directory is None:
                raise ThreadGoalSessionNotFoundError(f"session not found: {thread_id}")
            record = self._fold_record(
                self._read_entries(directory),
                expected_thread_id=thread_id,
            )
            entry, result = mutation(record)
            if entry.get("_type") != "thread_goal.noop":
                self._append_entry(directory, entry)
            return result

    @staticmethod
    def _ensure_fresh_identity(record: ThreadGoalRecord, goal_id: str) -> None:
        if goal_id in record.seen_goal_ids:
            raise ThreadGoalIdentityRetiredError(
                f"Goal identity {goal_id} was cleared and cannot be reused"
            )

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

    @classmethod
    def _updated_snapshot(
        cls,
        current: ThreadGoal | None,
        *,
        expected_goal_id: str,
        transform: Callable[[ThreadGoal], ThreadGoal],
        reason: str,
        source: str,
        turn_id: str | None,
    ) -> tuple[dict[str, Any], ThreadGoal]:
        matched = cls._matching_goal(current, expected_goal_id)
        goal = transform(matched)
        if goal.id != matched.id or goal.thread_id != matched.thread_id:
            raise ThreadGoalConflictError("Goal identity cannot change during update")
        if goal == matched:
            return cls._noop_entry(), matched
        if goal.created_at != matched.created_at:
            raise ThreadGoalConflictError("Goal created_at cannot change")
        if goal.updated_at < matched.updated_at:
            raise ThreadGoalConflictError("Goal updated_at cannot move backwards")
        return (
            cls._snapshot_entry(
                goal,
                reason=reason,
                source=source,
                turn_id=turn_id,
            ),
            goal,
        )

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
        seen_goal_ids: set[str] = set()
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
                if current is not None:
                    seen_goal_ids.add(current.id)
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
                    seen_goal_ids.add(goal.id)
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
            if current is not None:
                seen_goal_ids.add(current.id)
            outcome = None
        if current is not None and current.thread_id != expected_thread_id:
            raise ThreadGoalLedgerCorruptError(
                "Goal snapshot belongs to another Session"
            )
        return ThreadGoalRecord(
            goal=current,
            outcome=outcome,
            seen_goal_ids=frozenset(seen_goal_ids),
            turn_settlement_ids=(
                cls._turn_settlement_ids(entries, goal_id=current.id)
                if current is not None
                else frozenset()
            ),
        )

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
        cls._discard_uncommitted_tail(path)
        encoded = (
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        descriptor = open_private_file(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        )
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
    "ThreadGoalIdentityRetiredError",
    "ThreadGoalLedgerCorruptError",
    "ThreadGoalRecord",
    "ThreadGoalSessionNotFoundError",
    "ThreadGoalStore",
    "ThreadGoalStoreError",
]
