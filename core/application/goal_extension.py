"""Thin Thread Goal extension over the ordinary Turn lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Any

from core.agent_runtime.goal_runtime import GoalRuntimeContext, GoalRuntimeHandler
from core.application.errors import (
    ConflictError,
    GoalNotFoundError,
    InvalidArgumentError,
    TurnAlreadyRunningError,
    TurnNotFoundError,
)
from core.application.goal_turn_port import GoalTurnAssociation, GoalTurnPort
from core.application.views import thread_goal_view
from core.domain.item import ItemKind
from core.domain.message_provenance import ClientSurface, TurnInputSource
from core.domain.thread_goal import (
    GOAL_OBJECTIVE_INPUT_MAX_CHARS,
    GOAL_OUTCOME_EVIDENCE_MAX_ITEMS,
    GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS,
    GOAL_OUTCOME_REASON_MAX_CHARS,
    GoalEvidenceRef,
    GoalOutcome,
    ThreadGoal,
    ThreadGoalStatus,
)
from core.domain.turn import Turn, TurnStatus
from core.sessions.thread_goal_store import (
    ThreadGoalConflictError,
    ThreadGoalIdentityRetiredError,
    ThreadGoalRecord,
    ThreadGoalStore,
)
from core.skills.models import MAX_SELECTED_SKILLS, SkillSelection

logger = logging.getLogger(__name__)
_EVIDENCE_KINDS = frozenset(
    {
        ItemKind.TOOL_CALL,
        ItemKind.COMMAND_EXECUTION,
        ItemKind.FILE_CHANGE,
        ItemKind.DIFF,
        ItemKind.TEST_RESULT,
        ItemKind.ARTIFACT,
    }
)

GoalUpdateSink = Callable[[str, ThreadGoal | None], None]
GoalLifecycleSink = Callable[[str, str, dict[str, Any]], None]
GoalContinuationGuard = Callable[[ThreadGoal], None]


class GoalContinueDisposition(StrEnum):
    STARTED = "started"
    ALREADY_RUNNING = "alreadyRunning"


@dataclass(frozen=True, slots=True)
class GoalContinueResult:
    goal: ThreadGoal
    disposition: GoalContinueDisposition
    turn_id: str


class GoalContinuationRejected(ConflictError):
    """A durable owner or settlement invariant prevents continuation."""


class GoalIdentityRetiredError(ConflictError):
    """A caller-owned Goal was explicitly cleared and cannot be recreated."""


class GoalExtension(GoalRuntimeHandler):
    """Persist one optional Goal and react to ordinary Turn lifecycle events."""

    def __init__(
        self,
        store: ThreadGoalStore,
        turns: GoalTurnPort,
        *,
        update_sink: GoalUpdateSink | None = None,
        lifecycle_sink: GoalLifecycleSink | None = None,
    ) -> None:
        self.store = store
        self.turns = turns
        self._update_sink = update_sink
        self._lifecycle_sink = lifecycle_sink
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._continuation_guards: list[GoalContinuationGuard] = []
        self._continuation_template = (
            files("core.application.goal_prompts")
            .joinpath("continuation.md")
            .read_text(encoding="utf-8")
            .strip()
        )

    def add_continuation_guard(self, guard: GoalContinuationGuard) -> None:
        """Register an owner policy without coupling Goal to that owner."""

        if guard not in self._continuation_guards:
            self._continuation_guards.append(guard)

    def read(self, thread_id: str) -> ThreadGoal | None:
        return self.store.read(thread_id)

    def is_turn_accounted(
        self,
        thread_id: str,
        *,
        goal_id: str,
        turn_id: str,
    ) -> bool:
        """Report whether Goal usage settlement for a Turn is durable."""

        return self.store.has_turn_settlement(
            thread_id,
            goal_id=goal_id,
            turn_id=turn_id,
        )

    def are_turns_accounted(
        self,
        thread_id: str,
        *,
        goal_id: str,
        turn_ids: tuple[str, ...],
    ) -> bool:
        return self.store.has_turn_settlements(
            thread_id,
            goal_id=goal_id,
            turn_ids=turn_ids,
        )

    def read_outcome(self, thread_id: str) -> GoalOutcome | None:
        outcome = self.store.read_record(thread_id).outcome
        if outcome is None or outcome.decided_by_turn_id is None:
            return outcome
        try:
            snapshot = self.turns.read(outcome.decided_by_turn_id)
        except TurnNotFoundError as exc:
            logger.info(
                "Goal outcome Turn %s is unavailable: %s",
                outcome.decided_by_turn_id,
                exc,
            )
            return outcome
        evidence = tuple(
            GoalEvidenceRef(
                item_id=item.id,
                turn_id=item.turn_id,
                kind=item.kind.value,
                status=item.status.value,
                summary=(item.summary.strip() or item.kind.value.replace("_", " "))[
                    :GOAL_OUTCOME_EVIDENCE_SUMMARY_MAX_CHARS
                ],
            )
            for item in sorted(snapshot.items, key=lambda candidate: candidate.ordinal)
            if item.kind in _EVIDENCE_KINDS
        )[:GOAL_OUTCOME_EVIDENCE_MAX_ITEMS]
        return replace(outcome, evidence_refs=evidence)

    def read_guarded(self, thread_id: str, directory: Path) -> ThreadGoal | None:
        return self.store.read_guarded(thread_id, directory)

    def turn_association(self, thread_id: str) -> GoalTurnAssociation | None:
        record = self.store.read_record(thread_id)
        goal = record.goal
        if goal is None or goal.status is not ThreadGoalStatus.ACTIVE:
            return None
        return GoalTurnAssociation(
            goal_id=goal.id,
            skill_ids=goal.skill_ids,
            turn_settlement_ids=record.turn_settlement_ids,
        )

    @contextmanager
    def turn_submission_scope(
        self,
        thread_id: str,
    ) -> Iterator[GoalTurnAssociation | None]:
        """Freeze Goal association until the proposed Turn has committed.

        The Session file lock is held only across the short SQLite submission
        transaction. This closes clear/replace races without moving Goal state
        into TurnService or introducing an Automation-specific branch.
        """

        with self._lock(thread_id), self.store.guarded_record(thread_id) as record:
            goal = record.goal
            yield (
                GoalTurnAssociation(
                    goal_id=goal.id,
                    skill_ids=goal.skill_ids,
                    turn_settlement_ids=record.turn_settlement_ids,
                )
                if goal is not None and goal.status is ThreadGoalStatus.ACTIVE
                else None
            )

    def create(
        self,
        thread_id: str,
        *,
        objective: str,
        token_budget: int | None = None,
        skill_ids: tuple[str, ...] = (),
        start: bool = True,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> ThreadGoal:
        goal = ThreadGoal(
            thread_id=thread_id,
            objective=self._objective(objective),
            token_budget=token_budget,
            skill_ids=self._skills(skill_ids),
        )
        with self._lock(thread_id):
            try:
                created = self.store.create(goal)
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            self._notify(thread_id, created)
            self._emit(
                thread_id,
                "goal.created",
                {"goalId": created.id, "status": created.status.value},
            )
            if start:
                self._continue_if_idle_locked(
                    created,
                    client_surface=client_surface,
                )
            return self.read(thread_id) or created

    def provision(
        self,
        thread_id: str,
        *,
        goal_id: str,
        objective: str,
        token_budget: int | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> ThreadGoal:
        """Idempotently provision one caller-owned Goal without starting a Turn.

        Callers may persist ``goal_id`` before invoking this method and safely
        retry after a crash. A retry must present the same declarative Goal
        content; a different identity or content fails with a conflict.
        """

        requested = self._caller_owned_goal(
            thread_id,
            goal_id=goal_id,
            objective=objective,
            token_budget=token_budget,
            skill_ids=skill_ids,
        )
        with self._lock(thread_id):
            try:
                provisioned, created = self.store.provision(requested)
            except ThreadGoalIdentityRetiredError as exc:
                raise GoalIdentityRetiredError(str(exc)) from exc
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            if created:
                self._notify(thread_id, provisioned)
                self._emit(
                    thread_id,
                    "goal.created",
                    {
                        "goalId": provisioned.id,
                        "status": provisioned.status.value,
                    },
                )
            return provisioned

    def provision_replacing_completed(
        self,
        thread_id: str,
        *,
        expected_current_goal_id: str,
        goal_id: str,
        objective: str,
        token_budget: int | None = None,
        skill_ids: tuple[str, ...] = (),
    ) -> ThreadGoal:
        """Atomically replace one exact completed Goal with a fresh identity."""

        requested = self._caller_owned_goal(
            thread_id,
            goal_id=goal_id,
            objective=objective,
            token_budget=token_budget,
            skill_ids=skill_ids,
        )
        with self._lock(thread_id):
            try:
                provisioned = self.store.provision_replacing_completed(
                    requested,
                    expected_current_goal_id=expected_current_goal_id,
                )
            except ThreadGoalIdentityRetiredError as exc:
                raise GoalIdentityRetiredError(str(exc)) from exc
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            self._notify(thread_id, provisioned)
            self._emit(
                thread_id,
                "goal.cleared",
                {"goalId": expected_current_goal_id},
            )
            self._emit(
                thread_id,
                "goal.created",
                {
                    "goalId": provisioned.id,
                    "status": provisioned.status.value,
                },
            )
            return provisioned

    def edit(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        objective: str,
        token_budget: int | None,
        skill_ids: tuple[str, ...],
        continue_work: bool = True,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> ThreadGoal:
        clean_objective = self._objective(objective)
        clean_skills = self._skills(skill_ids)
        with self._lock(thread_id):
            try:
                updated = self.store.update(
                    thread_id,
                    expected_goal_id=expected_goal_id,
                    transform=lambda current: current.edit(
                        clean_objective,
                        token_budget=token_budget,
                        skill_ids=clean_skills,
                    ),
                    reason="objective edited",
                    source="user",
                )
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            self._notify(thread_id, updated)
            active = self.turns.executing_for_thread(thread_id)
            injected = False
            if (
                active is not None
                and active.goal_id == updated.id
                and updated.status is ThreadGoalStatus.ACTIVE
            ):
                injected = self.turns.inject_goal_update(
                    active.id,
                    message_id=self._goal_update_message_id(updated),
                    goal_id=updated.id,
                    objective=updated.objective,
                )
            if continue_work and not injected:
                self._continue_if_idle_locked(
                    updated,
                    client_surface=client_surface,
                )
            self._emit(
                thread_id,
                "goal.updated",
                {
                    "goalId": updated.id,
                    "status": updated.status.value,
                    "injectedIntoTurnId": active.id if injected and active else None,
                },
            )
            return updated

    def pause(self, thread_id: str, *, expected_goal_id: str) -> ThreadGoal:
        return self._user_transition(
            thread_id,
            expected_goal_id=expected_goal_id,
            status=ThreadGoalStatus.PAUSED,
            reason="paused by user",
        )

    def resume(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ThreadGoal:
        with self._lock(thread_id):
            updated = self._user_transition_locked(
                thread_id,
                expected_goal_id=expected_goal_id,
                status=ThreadGoalStatus.ACTIVE,
                reason="resumed by user",
            )
            self._continue_if_idle_locked(
                updated,
                client_surface=client_surface,
                connection_id=connection_id,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            return updated

    def clear(self, thread_id: str, *, expected_goal_id: str) -> None:
        with self._lock(thread_id):
            try:
                changed = self.store.clear(
                    thread_id,
                    expected_goal_id=expected_goal_id,
                )
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            if not changed:
                return
            self._notify(thread_id, None)
            self._emit(
                thread_id,
                "goal.cleared",
                {"goalId": expected_goal_id},
            )

    def continue_goal(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> GoalContinueResult:
        """Explicitly continue one active Goal through the ordinary Turn runtime."""

        with self._lock(thread_id):
            goal = self.read(thread_id)
            if goal is None:
                raise GoalNotFoundError(f"no Goal is attached to Session {thread_id}")
            if goal.id != expected_goal_id:
                raise ConflictError(
                    f"Goal identity changed: expected {expected_goal_id}, actual {goal.id}"
                )
            if goal.status is not ThreadGoalStatus.ACTIVE:
                raise ConflictError(
                    "only an active idle Goal can be continued; "
                    f"current status is {goal.status.value}"
                )
            return self._start_continuation_locked(
                goal,
                client_surface=client_surface,
                connection_id=connection_id,
                model=model,
                reasoning_effort=reasoning_effort,
            )

    def continue_if_idle(self, thread_id: str) -> None:
        with self._lock(thread_id):
            goal = self.read(thread_id)
            if goal is not None:
                self._continue_if_idle_locked(goal)

    def reconcile_turn_settlements(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        continue_from_latest_completion: bool = False,
    ) -> int:
        """Account durable terminal Turns for one exact current Goal.

        Reconciliation is idempotent and never submits work by default. An
        owning runtime may explicitly restore the ordinary settled-listener
        behavior when the latest Goal Turn completed normally. Interrupted,
        failed, non-terminal, replaced, and cleared Goals are never replayed.
        """

        with self._lock(thread_id):
            current = self.read(thread_id)
            if current is None or current.id != expected_goal_id:
                return 0
            goal_turns = self.turns.list_for_goal(thread_id, expected_goal_id)
            applied = 0
            for turn in goal_turns:
                if not turn.status.is_terminal:
                    continue
                _, settled = self._account_goal_turn_locked(turn)
                applied += int(settled)

            latest = goal_turns[-1] if goal_turns else None
            current = self.read(thread_id)
            if (
                continue_from_latest_completion
                and current is not None
                and current.id == expected_goal_id
                and current.status is ThreadGoalStatus.ACTIVE
                and latest is not None
                and latest.status is TurnStatus.COMPLETED
            ):
                self._continue_if_idle_locked(current)
            return applied

    def read_goal(self, context: GoalRuntimeContext) -> dict[str, Any]:
        goal = self.read(context.thread_id)
        return {
            "goal": thread_goal_view(goal) if goal is not None else None,
            "ownedGoalId": context.goal_id,
            "canUpdate": bool(
                goal is not None
                and goal.id == context.goal_id
                and goal.status is ThreadGoalStatus.ACTIVE
            ),
        }

    def update_goal(
        self,
        context: GoalRuntimeContext,
        *,
        status: str,
        reason: str | None,
    ) -> dict[str, Any]:
        try:
            requested = ThreadGoalStatus(status)
        except ValueError as exc:
            raise InvalidArgumentError(
                "update_goal status must be complete or blocked"
            ) from exc
        if requested not in {ThreadGoalStatus.COMPLETE, ThreadGoalStatus.BLOCKED}:
            raise InvalidArgumentError("update_goal status must be complete or blocked")
        turn = self.turns.read(context.turn_id).turn
        if (
            turn.thread_id != context.thread_id
            or turn.goal_id != context.goal_id
            or turn.status.is_terminal
        ):
            raise ConflictError("this Turn no longer owns the current Goal")
        clean_reason = self._reason(
            reason,
            fallback=(
                "Agent marked the Goal complete."
                if requested is ThreadGoalStatus.COMPLETE
                else "Agent reported a persistent blocker."
            ),
        )
        with self._lock(context.thread_id):
            try:
                updated = self.store.update(
                    context.thread_id,
                    expected_goal_id=context.goal_id,
                    transform=lambda current: current.agent_transition(requested),
                    reason=clean_reason,
                    source="agent",
                    turn_id=context.turn_id,
                )
            except ThreadGoalConflictError as exc:
                raise ConflictError(str(exc)) from exc
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            self._notify(context.thread_id, updated)
            self._emit(
                context.thread_id,
                (
                    "goal.completed"
                    if requested is ThreadGoalStatus.COMPLETE
                    else "goal.blocked"
                ),
                {
                    "goalId": updated.id,
                    "turnId": context.turn_id,
                    "reason": clean_reason,
                },
            )
            return {
                "goal": thread_goal_view(updated),
                "updated": True,
            }

    def recover_incomplete(self) -> int:
        """Replay committed Goal Turns whose ledger settlement may be missing."""

        recovered = 0
        for discovered in self.store.list_current():
            with self._lock(discovered.thread_id):
                current = self.read(discovered.thread_id)
                if current is None or current.id != discovered.id:
                    continue
                turns = self.turns.list_for_goal(current.thread_id, current.id)
                for turn in turns:
                    if not turn.status.is_terminal:
                        continue
                    _, applied = self._account_goal_turn_locked(turn)
                    recovered += int(applied)

                latest = turns[-1] if turns else None
                current = self.read(discovered.thread_id)
                if (
                    current is not None
                    and current.id == discovered.id
                    and current.status is ThreadGoalStatus.ACTIVE
                    and latest is not None
                    and latest.status is not TurnStatus.INTERRUPTED
                ):
                    self._continue_if_idle_locked(current)
        return recovered

    def on_turn_settled(self, turn: Turn) -> None:
        """Account a durable Turn and continue only from normal completion."""

        if turn.goal_id is None:
            if turn.status is TurnStatus.COMPLETED:
                self.continue_if_idle(turn.thread_id)
            return
        with self._lock(turn.thread_id):
            updated, _ = self._account_goal_turn_locked(turn)
            goal_turns = (
                self.turns.list_for_goal(turn.thread_id, turn.goal_id)
                if updated is not None
                else ()
            )
            if (
                updated is not None
                and turn.status is TurnStatus.COMPLETED
                and goal_turns
                and goal_turns[-1].id == turn.id
            ):
                self._continue_if_idle_locked(updated)

    def _account_goal_turn_locked(
        self,
        turn: Turn,
    ) -> tuple[ThreadGoal | None, bool]:
        goal = self.read(turn.thread_id)
        if goal is None or goal.id != turn.goal_id:
            return None, False
        tokens = self._turn_tokens(turn.id)
        elapsed = self._turn_elapsed_seconds(turn)
        reason = {
            TurnStatus.COMPLETED: "turn completed",
            TurnStatus.INTERRUPTED: "turn interrupted",
            TurnStatus.FAILED: self._runtime_failure_reason(turn),
        }.get(turn.status, "turn settled")
        try:
            updated, applied = self.store.settle_turn(
                turn.thread_id,
                expected_goal_id=goal.id,
                turn_id=turn.id,
                transform=lambda current: self._settled_goal(
                    current,
                    turn=turn,
                    tokens=tokens,
                    elapsed_seconds=elapsed,
                ),
                reason=reason,
            )
        except ThreadGoalConflictError:
            logger.info(
                "Goal changed while Turn %s was being accounted",
                turn.id,
            )
            return None, False
        if not applied:
            return updated, False
        self._notify(turn.thread_id, updated)
        if (
            turn.status is TurnStatus.FAILED
            and updated.status is ThreadGoalStatus.BLOCKED
        ):
            self._emit(
                turn.thread_id,
                "goal.blocked",
                {
                    "goalId": updated.id,
                    "turnId": turn.id,
                    "reason": reason,
                },
            )
        return updated, True

    def _user_transition(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        status: ThreadGoalStatus,
        reason: str,
    ) -> ThreadGoal:
        with self._lock(thread_id):
            return self._user_transition_locked(
                thread_id,
                expected_goal_id=expected_goal_id,
                status=status,
                reason=reason,
            )

    def _user_transition_locked(
        self,
        thread_id: str,
        *,
        expected_goal_id: str,
        status: ThreadGoalStatus,
        reason: str,
    ) -> ThreadGoal:
        try:
            updated = self.store.update(
                thread_id,
                expected_goal_id=expected_goal_id,
                transform=lambda current: current.user_transition(status),
                reason=reason,
                source="user",
                guard=(
                    self._ensure_continuation_allowed_record
                    if status is ThreadGoalStatus.ACTIVE
                    else None
                ),
            )
        except ThreadGoalConflictError as exc:
            raise ConflictError(str(exc)) from exc
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        self._notify(thread_id, updated)
        self._emit(
            thread_id,
            f"goal.{status.value}",
            {"goalId": updated.id, "status": updated.status.value},
        )
        return updated

    def _continue_if_idle_locked(
        self,
        goal: ThreadGoal,
        *,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        latest = self.read(goal.thread_id)
        if (
            latest is None
            or latest.id != goal.id
            or latest.status is not ThreadGoalStatus.ACTIVE
        ):
            return
        try:
            self._start_continuation_locked(
                latest,
                client_surface=client_surface,
                connection_id=connection_id,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        except GoalContinuationRejected as exc:
            logger.info("Goal %s remains idle: %s", latest.id, exc)

    def _start_continuation_locked(
        self,
        goal: ThreadGoal,
        *,
        client_surface: ClientSurface,
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> GoalContinueResult:
        self._ensure_continuation_allowed(goal)
        active = self.turns.active_for_thread(goal.thread_id)
        if active is not None:
            return GoalContinueResult(
                goal=goal,
                disposition=GoalContinueDisposition.ALREADY_RUNNING,
                turn_id=active.id,
            )
        try:
            snapshot = self.turns.start(
                goal.thread_id,
                prompt=self._continuation_prompt(goal),
                connection_id=connection_id,
                model=model,
                reasoning_effort=reasoning_effort,
                client_surface=client_surface,
                input_source=TurnInputSource.GOAL_CONTINUATION,
                expected_goal_id=goal.id,
            )
        except TurnAlreadyRunningError as exc:
            actual_turn_id = exc.details.get("actualTurnId")
            if not isinstance(actual_turn_id, str) or not actual_turn_id:
                active = self.turns.active_for_thread(goal.thread_id)
                if active is None:
                    raise
                actual_turn_id = active.id
            return GoalContinueResult(
                goal=goal,
                disposition=GoalContinueDisposition.ALREADY_RUNNING,
                turn_id=actual_turn_id,
            )
        return GoalContinueResult(
            goal=goal,
            disposition=GoalContinueDisposition.STARTED,
            turn_id=snapshot.turn.id,
        )

    def _ensure_continuation_allowed(self, goal: ThreadGoal) -> None:
        terminal_turn_ids = tuple(
            turn.id
            for turn in self.turns.list_for_goal(goal.thread_id, goal.id)
            if turn.status.is_terminal
        )
        if terminal_turn_ids and not self.store.has_turn_settlements(
            goal.thread_id,
            goal_id=goal.id,
            turn_ids=terminal_turn_ids,
        ):
            raise GoalContinuationRejected(
                "Goal Turn settlement is incomplete; recover it before continuing"
            )
        for guard in tuple(self._continuation_guards):
            guard(goal)

    def _ensure_continuation_allowed_record(
        self,
        record: ThreadGoalRecord,
    ) -> None:
        """Revalidate a resume inside the canonical Goal mutation lock."""

        goal = record.goal
        if goal is None:
            raise GoalContinuationRejected("Goal no longer exists")
        terminal_turn_ids = tuple(
            turn.id
            for turn in self.turns.list_for_goal(goal.thread_id, goal.id)
            if turn.status.is_terminal
        )
        if any(
            turn_id not in record.turn_settlement_ids for turn_id in terminal_turn_ids
        ):
            raise GoalContinuationRejected(
                "Goal Turn settlement is incomplete; recover it before continuing"
            )
        for guard in tuple(self._continuation_guards):
            guard(goal)

    def _continuation_prompt(self, goal: ThreadGoal) -> str:
        return self._continuation_template.replace(
            "{{OBJECTIVE}}",
            goal.objective,
        )

    def _turn_tokens(self, turn_id: str) -> int:
        for item in reversed(self.turns.read(turn_id).items):
            if item.kind is not ItemKind.COMPLETION:
                continue
            usage = item.payload.get("usage")
            if not isinstance(usage, dict):
                return 0
            total = usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool):
                return max(0, total)
            return sum(
                value
                for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens"}
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
            )
        return 0

    @staticmethod
    def _turn_elapsed_seconds(turn: Turn) -> int:
        if turn.started_at is None or turn.completed_at is None:
            return 0
        return max(0, int((turn.completed_at - turn.started_at).total_seconds()))

    @staticmethod
    def _settled_goal(
        goal: ThreadGoal,
        *,
        turn: Turn,
        tokens: int,
        elapsed_seconds: int,
    ) -> ThreadGoal:
        updated = goal.add_usage(tokens=tokens, elapsed_seconds=elapsed_seconds)
        if turn.status is TurnStatus.FAILED:
            updated = updated.block_after_error()
        return updated

    def _notify(self, thread_id: str, goal: ThreadGoal | None) -> None:
        if self._update_sink is None:
            return
        try:
            self._update_sink(thread_id, goal)
        except Exception:
            logger.exception("failed to publish Goal update for %s", thread_id)

    def _emit(
        self,
        thread_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if self._lifecycle_sink is None:
            return
        try:
            self._lifecycle_sink(thread_id, event_type, payload)
        except Exception:
            logger.exception(
                "failed to publish Goal lifecycle event %s for %s",
                event_type,
                thread_id,
            )

    def _lock(self, thread_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(thread_id, threading.RLock())

    def _caller_owned_goal(
        self,
        thread_id: str,
        *,
        goal_id: str,
        objective: str,
        token_budget: int | None,
        skill_ids: tuple[str, ...],
    ) -> ThreadGoal:
        try:
            return ThreadGoal(
                id=goal_id,
                thread_id=thread_id,
                objective=self._objective(objective),
                token_budget=token_budget,
                skill_ids=self._skills(skill_ids),
            )
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc

    @staticmethod
    def _objective(value: str) -> str:
        clean = value.strip()
        if not clean:
            raise InvalidArgumentError("Goal objective must not be empty")
        if len(clean) > GOAL_OBJECTIVE_INPUT_MAX_CHARS:
            raise InvalidArgumentError(
                "Goal objective may contain at most "
                f"{GOAL_OBJECTIVE_INPUT_MAX_CHARS} characters"
            )
        return clean

    @staticmethod
    def _skills(values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            clean = tuple(SkillSelection(skill_id=value).skill_id for value in values)
        except (TypeError, ValueError) as exc:
            raise InvalidArgumentError(str(exc)) from exc
        if len(clean) > MAX_SELECTED_SKILLS:
            raise InvalidArgumentError(
                f"a Goal may select at most {MAX_SELECTED_SKILLS} Skills"
            )
        if len(set(clean)) != len(clean):
            raise InvalidArgumentError("Goal Skill IDs must be unique")
        return clean

    @staticmethod
    def _reason(value: str | None, *, fallback: str) -> str:
        clean = value.strip() if value is not None else ""
        clean = clean or fallback
        if len(clean) > GOAL_OUTCOME_REASON_MAX_CHARS:
            raise InvalidArgumentError(
                "Goal update reason may contain at most "
                f"{GOAL_OUTCOME_REASON_MAX_CHARS} characters"
            )
        return clean

    @staticmethod
    def _runtime_failure_reason(turn: Turn) -> str:
        clean = (turn.error_message or "").strip()
        return (clean or "Goal Turn failed.")[:GOAL_OUTCOME_REASON_MAX_CHARS]

    @staticmethod
    def _goal_update_message_id(goal: ThreadGoal) -> str:
        stamp = int(goal.updated_at.timestamp() * 1_000_000)
        return f"goal-update:{goal.id}:{stamp}"


__all__ = [
    "GoalContinueDisposition",
    "GoalContinueResult",
    "GoalExtension",
    "GoalLifecycleSink",
    "GoalUpdateSink",
]
