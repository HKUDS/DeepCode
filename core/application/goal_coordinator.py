"""Durable cross-Turn orchestration for active Session Goals."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from core.application.errors import ConflictError, TurnAlreadyRunningError
from core.application.execution_registry import ExecutionRegistry
from core.application.goal_evaluator import GoalEvaluationContext, GoalEvaluator
from core.application.goal_service import GoalService
from core.application.goal_turn_port import GoalTurnPort
from core.domain.goal import (
    GoalAttempt,
    GoalAttemptStatus,
    GoalEvaluation,
    GoalRecord,
    GoalStatus,
    GoalVerdict,
)
from core.domain.item import ItemKind
from core.domain.turn import Turn, TurnStatus
from core.events import Event


_PROMPT_DIRECTORY = Path(__file__).with_name("goal_prompts")
logger = logging.getLogger(__name__)


class GoalCoordinator:
    """Start, evaluate, and continue ordinary Turns for one active Goal."""

    def __init__(
        self,
        goals: GoalService,
        turns: GoalTurnPort,
        evaluator: GoalEvaluator,
        registry: ExecutionRegistry,
    ) -> None:
        self.goals = goals
        self.turns = turns
        self.evaluator = evaluator
        self.registry = registry
        self._initial_prompt = _read_prompt("initial.md")
        self._continuation_prompt = _read_prompt("continuation.md")
        self._observer_lock = threading.Lock()
        self._event_observers: dict[str, Callable[[Event], None]] = {}

    def add_event_observer(
        self,
        thread_id: str,
        observer: Callable[[Event], None],
    ) -> None:
        """Attach a process-local renderer without putting UI policy in core."""

        with self._observer_lock:
            self._event_observers[thread_id] = observer

    def remove_event_observer(
        self,
        thread_id: str,
        observer: Callable[[Event], None] | None = None,
    ) -> None:
        with self._observer_lock:
            current = self._event_observers.get(thread_id)
            if observer is None or current is observer:
                self._event_observers.pop(thread_id, None)

    def pause(self, thread_id: str, *, expected_revision: int) -> GoalRecord:
        """Stop continuation and interrupt a locally owned active Turn."""

        record = self.goals.pause(
            thread_id,
            expected_revision=expected_revision,
        )
        active = self.turns.active_for_thread(thread_id)
        if active is not None and active.goal_id == record.goal.id:
            self.turns.interrupt(active.id)
        elif (
            record.latest_attempt is not None
            and not record.latest_attempt.status.is_terminal
        ):
            self.goals.update_attempt(
                thread_id,
                expected_revision=record.goal.revision,
                attempt_id=record.latest_attempt.id,
                status=GoalAttemptStatus.INTERRUPTED,
            )
        refreshed = self.goals.read(thread_id)
        return refreshed or record

    def resume(self, thread_id: str, *, expected_revision: int) -> GoalRecord:
        record = self.goals.resume(
            thread_id,
            expected_revision=expected_revision,
        )
        self.continue_if_idle(thread_id)
        return self.goals.read(thread_id) or record

    def clear(self, thread_id: str, *, expected_revision: int) -> None:
        record = self.goals.read(thread_id)
        if record is None:
            return
        active = self.turns.active_for_thread(thread_id)
        if active is not None and active.goal_id == record.goal.id:
            self.turns.interrupt(active.id)
        self.goals.clear(thread_id, expected_revision=expected_revision)

    def start(self, thread_id: str) -> GoalRecord:
        record = self.goals.read(thread_id)
        if record is None:
            raise ConflictError("cannot start a missing Goal")
        if record.goal.status is not GoalStatus.ACTIVE:
            raise ConflictError("only an active Goal can start")
        self.continue_if_idle(thread_id)
        refreshed = self.goals.read(thread_id)
        assert refreshed is not None
        return refreshed

    def recover_incomplete(self) -> int:
        """Pause Goals left active by an unclean application shutdown.

        Goal Turns may mutate a workspace, so replay after a crash is never
        implicit. The durable Attempt is first marked interrupted and the Goal
        is then paused. A user can inspect evidence and explicitly resume.
        """

        recovered = 0
        summaries = self.goals.store.sessions.list_sessions(limit=100_000)
        for summary in summaries:
            try:
                record = self.goals.read(summary.session_id)
                if record is None or record.goal.status is not GoalStatus.ACTIVE:
                    continue
                latest = record.latest_attempt
                if latest is not None and not latest.status.is_terminal:
                    self.goals.update_attempt(
                        summary.session_id,
                        expected_revision=record.goal.revision,
                        attempt_id=latest.id,
                        status=GoalAttemptStatus.INTERRUPTED,
                    )
                    refreshed = self.goals.read(summary.session_id)
                    if refreshed is None:
                        continue
                    record = refreshed
                self.goals.pause(
                    summary.session_id,
                    expected_revision=record.goal.revision,
                    reason=(
                        "Paused after DeepCode restarted. Review the latest "
                        "Attempt before resuming to avoid repeating mutations."
                    ),
                )
                recovered += 1
            except ConflictError:
                logger.info(
                    "Goal changed while restart recovery inspected %s",
                    summary.session_id,
                )
            except Exception:  # noqa: BLE001 - isolate corrupt/unavailable Sessions
                logger.exception(
                    "Goal restart recovery failed for %s",
                    summary.session_id,
                )
        return recovered

    def prepare_shutdown(self) -> int:
        """Durably pause locally owned Goal work before runtime cancellation."""

        paused = 0
        summaries = self.goals.store.sessions.list_sessions(limit=100_000)
        for summary in summaries:
            try:
                record = self.goals.read(summary.session_id)
                if record is None or record.goal.status is not GoalStatus.ACTIVE:
                    continue
                latest = record.latest_attempt
                active_turn = self.turns.active_for_thread(summary.session_id)
                owns_turn = (
                    active_turn is not None
                    and active_turn.goal_id == record.goal.id
                    and self.registry.is_active(active_turn.id)
                )
                owns_evaluation = (
                    latest is not None
                    and latest.turn_id is not None
                    and self.registry.is_active(self._evaluation_job_id(latest.turn_id))
                )
                if not owns_turn and not owns_evaluation:
                    continue
                record = self.goals.pause(
                    summary.session_id,
                    expected_revision=record.goal.revision,
                    reason="Paused because DeepCode is shutting down.",
                )
                latest = record.latest_attempt
                if latest is not None and not latest.status.is_terminal:
                    self.goals.update_attempt(
                        summary.session_id,
                        expected_revision=record.goal.revision,
                        attempt_id=latest.id,
                        status=GoalAttemptStatus.INTERRUPTED,
                    )
                paused += 1
            except ConflictError:
                logger.info(
                    "Goal changed while shutdown inspected %s",
                    summary.session_id,
                )
            except Exception:  # noqa: BLE001 - shutdown must continue
                logger.exception(
                    "Goal shutdown preparation failed for %s",
                    summary.session_id,
                )
        return paused

    @staticmethod
    def may_resume_queued_after_restart(turn: Turn) -> bool:
        """Only ordinary user Turns are safe to resume automatically."""

        return turn.goal_attempt_id is None

    def continue_if_idle(self, thread_id: str) -> None:
        record = self.goals.read(thread_id)
        if record is None or record.goal.status is not GoalStatus.ACTIVE:
            return
        if self.turns.active_for_thread(thread_id) is not None:
            return
        try:
            attempt = self.goals.begin_attempt(
                thread_id,
                expected_revision=record.goal.revision,
            )
        except ConflictError:
            return
        try:
            snapshot = self.turns.start_goal_attempt(
                thread_id,
                prompt=self._attempt_prompt(record),
                skill_ids=record.goal.skill_ids,
                goal_id=record.goal.id,
                goal_definition_revision=record.goal.definition_revision,
                goal_attempt_id=attempt.id,
                event_observer=self._event_observer(thread_id),
            )
        except TurnAlreadyRunningError:
            self._fail_unstarted_attempt(record, attempt, "A user Turn took priority.")
            return
        except Exception as exc:
            self._fail_unstarted_attempt(
                record,
                attempt,
                f"Goal Turn could not start: {type(exc).__name__}: {exc}",
            )
            raise
        try:
            self.goals.update_attempt(
                thread_id,
                expected_revision=record.goal.revision,
                attempt_id=attempt.id,
                status=GoalAttemptStatus.QUEUED,
                turn_id=snapshot.turn.id,
            )
            self.goals.update_attempt(
                thread_id,
                expected_revision=record.goal.revision,
                attempt_id=attempt.id,
                status=GoalAttemptStatus.RUNNING,
            )
        except ConflictError:
            # A very fast Turn may already have been settled and evaluated.
            return

    def on_turn_settled(self, turn: Turn) -> None:
        """Queue evaluation after the Turn and transcript are fully durable."""

        job_id = self._evaluation_job_id(turn.id)
        try:
            self.registry.start(job_id, lambda: self._settle(turn))
        except ValueError:
            return
        except RuntimeError:
            logger.info("Goal evaluation skipped during application shutdown")

    async def _settle(self, turn: Turn) -> None:
        record = self.goals.read(turn.thread_id)
        if record is None:
            return
        attempt = self._attempt_for_turn(record, turn)
        if record.goal.status is not GoalStatus.ACTIVE:
            if attempt is not None and not attempt.status.is_terminal:
                self._finish_attempt(
                    record,
                    attempt,
                    GoalAttemptStatus.INTERRUPTED,
                )
            return
        if attempt is None:
            try:
                attempt = self.goals.begin_attempt(
                    turn.thread_id,
                    expected_revision=record.goal.revision,
                    turn_id=turn.id,
                )
            except ConflictError:
                return
        elif attempt.turn_id is None:
            attempt = self.goals.update_attempt(
                turn.thread_id,
                expected_revision=record.goal.revision,
                attempt_id=attempt.id,
                status=attempt.status,
                turn_id=turn.id,
            )

        if attempt.goal_revision != record.goal.definition_revision:
            self._finish_attempt(
                record,
                attempt,
                GoalAttemptStatus.INTERRUPTED,
            )
            return
        if turn.status is TurnStatus.FAILED:
            self._finish_attempt(record, attempt, GoalAttemptStatus.FAILED)
            self.goals.block(
                turn.thread_id,
                expected_revision=record.goal.revision,
                reason=turn.error_message or "The Goal Turn failed.",
            )
            return
        if turn.status is TurnStatus.INTERRUPTED:
            self._finish_attempt(record, attempt, GoalAttemptStatus.INTERRUPTED)
            self.goals.pause(
                turn.thread_id,
                expected_revision=record.goal.revision,
            )
            return
        if turn.status is not TurnStatus.COMPLETED:
            return

        if attempt.status is GoalAttemptStatus.QUEUED:
            attempt = self.goals.update_attempt(
                turn.thread_id,
                expected_revision=record.goal.revision,
                attempt_id=attempt.id,
                status=GoalAttemptStatus.RUNNING,
            )
        attempt = self.goals.update_attempt(
            turn.thread_id,
            expected_revision=record.goal.revision,
            attempt_id=attempt.id,
            status=GoalAttemptStatus.EVALUATING,
        )
        try:
            context = GoalEvaluationContext(
                record=record,
                attempt=attempt,
                turn=self.turns.read(turn.id),
                workspace=self._workspace(turn.thread_id),
            )
            evaluation = await self.evaluator.evaluate(context)
        except Exception as exc:  # noqa: BLE001 - fail closed, never retry-loop
            evaluation = GoalEvaluation(
                goal_id=record.goal.id,
                goal_revision=record.goal.definition_revision,
                attempt_id=attempt.id,
                turn_id=turn.id,
                verdict=GoalVerdict.ERROR,
                reason=f"Goal evaluation failed: {type(exc).__name__}: {exc}",
            )
        work_tokens = self._turn_tokens(context.turn)
        if work_tokens:
            evaluation = replace(
                evaluation,
                tokens_used=evaluation.tokens_used + work_tokens,
            )
        evaluation = self._apply_stall_policy(record, evaluation)
        try:
            decided = self.goals.apply_evaluation(
                turn.thread_id,
                expected_revision=record.goal.revision,
                evaluation=evaluation,
            )
        except ConflictError:
            return
        self._finish_attempt(
            decided,
            attempt,
            GoalAttemptStatus.COMPLETED,
        )
        if decided.goal.status is GoalStatus.ACTIVE:
            self.continue_if_idle(turn.thread_id)

    def _finish_attempt(
        self,
        record: GoalRecord,
        attempt: GoalAttempt,
        status: GoalAttemptStatus,
    ) -> None:
        try:
            self.goals.update_attempt(
                record.goal.thread_id,
                expected_revision=record.goal.revision,
                attempt_id=attempt.id,
                status=status,
            )
        except ConflictError:
            logger.info("Goal Attempt changed before terminal status was recorded")

    def _fail_unstarted_attempt(
        self,
        record: GoalRecord,
        attempt: GoalAttempt,
        reason: str,
    ) -> None:
        self._finish_attempt(record, attempt, GoalAttemptStatus.FAILED)
        logger.info("%s", reason)

    @staticmethod
    def _attempt_for_turn(record: GoalRecord, turn: Turn) -> GoalAttempt | None:
        if turn.goal_attempt_id is not None:
            return next(
                (
                    attempt
                    for attempt in record.attempts
                    if attempt.id == turn.goal_attempt_id
                ),
                None,
            )
        return next(
            (attempt for attempt in record.attempts if attempt.turn_id == turn.id),
            None,
        )

    def _attempt_prompt(self, record: GoalRecord) -> str:
        goal = record.goal
        template = (
            self._initial_prompt
            if record.latest_evaluation is None
            else self._continuation_prompt
        )
        return (
            template.replace("{{OBJECTIVE}}", goal.objective)
            .replace(
                "{{ACCEPTANCE_CRITERIA}}",
                "\n".join(f"- {value}" for value in goal.acceptance_criteria)
                or "- The stated Goal is fully achieved.",
            )
            .replace(
                "{{REASON}}",
                goal.last_reason or "Completion has not been proven yet.",
            )
        )

    def _workspace(self, thread_id: str) -> str:
        session = self.goals.store.sessions.get_session(thread_id)
        if session is None:
            raise ConflictError(f"Session disappeared: {thread_id}")
        workspace = (session.metadata or {}).get("workspace")
        if not isinstance(workspace, str) or not workspace:
            raise ConflictError("Session has no canonical workspace")
        return workspace

    def _event_observer(
        self,
        thread_id: str,
    ) -> Callable[[Event], None] | None:
        with self._observer_lock:
            return self._event_observers.get(thread_id)

    @staticmethod
    def _turn_tokens(snapshot) -> int:
        for item in reversed(snapshot.items):
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
    def _evaluation_job_id(turn_id: str) -> str:
        return f"goal-evaluate:{turn_id}"

    @staticmethod
    def _apply_stall_policy(
        record: GoalRecord,
        evaluation: GoalEvaluation,
    ) -> GoalEvaluation:
        if evaluation.verdict is not GoalVerdict.CONTINUE:
            return evaluation
        recent = [
            item.reason.strip().casefold()
            for item in record.evaluations
            if item.verdict is GoalVerdict.CONTINUE
        ]
        recent.append(evaluation.reason.strip().casefold())
        threshold = record.goal.stall_threshold
        if len(recent) < threshold or len(set(recent[-threshold:])) != 1:
            return evaluation
        return replace(
            evaluation,
            verdict=GoalVerdict.BLOCKED,
            reason=(
                f"No measurable progress after {threshold} evaluations: "
                f"{evaluation.reason}"
            ),
        )


def _read_prompt(name: str) -> str:
    return (_PROMPT_DIRECTORY / name).read_text(encoding="utf-8").strip()


__all__ = ["GoalCoordinator"]
