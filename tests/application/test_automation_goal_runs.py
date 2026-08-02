from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from core.agent_runtime.goal_runtime import GoalRuntimeRouter
from core.application import DeepCodeApplication
from core.application.errors import AutomationNotFoundError, ConflictError
from core.application.goal_extension import GoalContinueDisposition
from core.domain import (
    AutomationOccurrence,
    AutomationRun,
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    AutomationTrigger,
    ExecutionPermissionMode,
    ThreadGoalStatus,
    TrustState,
    Turn,
    TurnStatus,
)
from core.domain.common import new_id, utc_now
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence.automation_repository import (
    AutomationOccurrenceRepository,
    AutomationRepository,
    AutomationRevisionRepository,
    AutomationRunRepository,
)
from core.persistence.execution_repository import TurnRepository
from core.sessions import SessionStore


_Decision = Literal["complete", "blocked"]


@dataclass(frozen=True, slots=True)
class _TurnStep:
    decision: _Decision | None
    gate: threading.Event | None = None


class _GoalAwareSession:
    """Small AgentSession that makes decisions through the real Goal router."""

    def __init__(
        self,
        factory: "_GoalAwareFactory",
        goal_runtime: GoalRuntimeRouter,
    ) -> None:
        self.factory = factory
        self.goal_runtime = goal_runtime
        self.history: list[dict[str, str]] = []
        self.closed = False

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        index, step = self.factory.claim_step(op.text)
        self.history.append({"role": "user", "content": op.text})
        yield Event(f"{index}:started", TurnStarted())

        # TurnService activates GoalRuntimeRouter only after consuming
        # TurnStarted. Decisions must happen after that boundary.
        while step.gate is not None and not step.gate.is_set():
            await asyncio.sleep(0.005)
        if step.decision is not None:
            self.goal_runtime.request(
                status=step.decision,
                reason=f"Agent requested {step.decision} in scripted Turn {index}.",
            )
            self.factory.record_decision(index, step.decision)

        final_text = (
            f"Turn {index} requested {step.decision}."
            if step.decision is not None
            else f"Turn {index} made progress and left the Goal active."
        )
        yield Event(f"{index}:message", AgentMessage(final_text))
        yield Event(f"{index}:complete", TaskComplete(final_text, "completed"))
        self.history.append({"role": "assistant", "content": final_text})

    async def aclose(self) -> None:
        self.closed = True


class _GoalAwareFactory:
    def __init__(self, *steps: _TurnStep) -> None:
        self.steps = steps
        self.sessions: list[_GoalAwareSession] = []
        self.started_prompts: list[str] = []
        self.decisions: list[tuple[int, _Decision]] = []
        self._lock = threading.Lock()

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        goal_runtime=None,
        **_kwargs,
    ):
        del workspace, model, approval_callback
        if goal_runtime is None:
            raise AssertionError("Automation Goal Turns require goal_runtime")
        session = _GoalAwareSession(self, goal_runtime)
        self.sessions.append(session)
        return session

    def claim_step(self, prompt: str) -> tuple[int, _TurnStep]:
        with self._lock:
            index = len(self.started_prompts)
            if index >= len(self.steps):
                raise AssertionError(
                    f"unexpected Automation Turn {index + 1}: {prompt!r}"
                )
            self.started_prompts.append(prompt)
            return index, self.steps[index]

    def record_decision(self, index: int, decision: _Decision) -> None:
        with self._lock:
            self.decisions.append((index, decision))


def _application(
    tmp_path: Path,
    factory: _GoalAwareFactory,
    *,
    prompt: str = "Inspect the repository and satisfy this automation.",
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        created = application.automations.create(
            project_id=project.id,
            name="Repository caretaker",
            prompt=prompt,
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        return application, created.automation
    except BaseException:
        application.close()
        raise


def _wait_until(predicate, description: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {description}")


def _wait_for_run(
    application: DeepCodeApplication,
    automation_id: str,
    run_id: str,
    status: AutomationRunStatus,
) -> AutomationRun:
    def matching_run():
        for run in application.automations.list_runs(automation_id):
            if run.id == run_id and run.status is status:
                return run
        return None

    return _wait_until(matching_run, f"Run {run_id} to become {status.value}")


def _runs(
    application: DeepCodeApplication,
    automation_id: str,
) -> tuple[AutomationRun, ...]:
    with application.database.read() as connection:
        return tuple(
            AutomationRunRepository(connection).list_for_automation(automation_id)
        )


def _turns(
    application: DeepCodeApplication,
    thread_id: str,
) -> tuple[Turn, ...]:
    with application.database.read() as connection:
        return tuple(TurnRepository(connection).list_for_thread(thread_id))


def _wait_for_turn_count(
    application: DeepCodeApplication,
    thread_id: str,
    expected: int,
) -> tuple[Turn, ...]:
    return _wait_until(
        lambda: (
            turns if len(turns := _turns(application, thread_id)) == expected else None
        ),
        f"Session {thread_id} to have {expected} Turns",
    )


def _fact_counts(
    application: DeepCodeApplication,
    automation_id: str,
    thread_id: str,
) -> tuple[int, int, int]:
    with application.database.read() as connection:
        occurrences = connection.execute(
            "SELECT COUNT(*) FROM automation_occurrences WHERE automation_id = ?",
            (automation_id,),
        ).fetchone()[0]
        runs = connection.execute(
            "SELECT COUNT(*) FROM automation_runs WHERE automation_id = ?",
            (automation_id,),
        ).fetchone()[0]
        turns = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
    return int(occurrences), int(runs), int(turns)


def test_manual_request_id_retry_reuses_one_occurrence_run_and_turn(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    request_id = "manual-request-001"
    try:
        first = application.automations.run_now(
            automation.id,
            request_id=request_id,
        )
        completed = _wait_for_run(
            application,
            automation.id,
            first.run.id,
            AutomationRunStatus.COMPLETED,
        )

        retried = application.automations.run_now(
            automation.id,
            request_id=request_id,
        )

        assert retried.run.id == completed.id
        assert retried.run.occurrence_id == completed.occurrence_id
        assert retried.run.turn_id == completed.turn_id
        assert completed.detail == "Agent requested complete in scripted Turn 0."
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )
        with application.database.read() as connection:
            occurrence = AutomationOccurrenceRepository(connection).get_by_key(
                automation.id,
                AutomationTrigger.MANUAL,
                request_id,
            )
        assert occurrence is not None
        assert occurrence.id == completed.occurrence_id
    finally:
        application.close()


def test_run_stays_open_across_turns_until_agent_completes_the_goal(
    tmp_path: Path,
) -> None:
    second_turn_gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep(None),
        _TurnStep("complete", gate=second_turn_gate),
    )
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="two-turn-goal",
        )
        turns = _wait_for_turn_count(application, automation.thread_id, 2)
        turns = _wait_until(
            lambda: (
                current
                if (current := _turns(application, automation.thread_id))[0].status
                is TurnStatus.COMPLETED
                and current[1].status is TurnStatus.RUNNING
                else None
            ),
            "the first Turn to settle while the second remains active",
        )
        open_run = next(
            run
            for run in _runs(application, automation.id)
            if run.id == execution.run.id
        )

        assert open_run.status is not AutomationRunStatus.COMPLETED
        assert open_run.completed_at is None
        assert open_run.goal_id is not None
        assert [turn.goal_id for turn in turns] == [
            open_run.goal_id,
            open_run.goal_id,
        ]
        assert [turn.execution_permission_mode for turn in turns] == [
            ExecutionPermissionMode.DEFAULT,
            ExecutionPermissionMode.DEFAULT,
        ]
        assert factory.decisions == []

        second_turn_gate.set()
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        assert completed.id == execution.run.id
        assert factory.decisions == [(1, "complete")]
        goal = application.goals.read(automation.thread_id)
        assert goal is not None
        assert goal.id == completed.goal_id
        assert goal.status is ThreadGoalStatus.COMPLETE
        assert all(
            turn.status is TurnStatus.COMPLETED
            for turn in _turns(application, automation.thread_id)
        )
    finally:
        second_turn_gate.set()
        application.close()


def test_prompt_update_creates_revision_while_open_run_keeps_old_instruction(
    tmp_path: Path,
) -> None:
    completion_gate = threading.Event()
    old_instruction = "Use the original immutable instruction."
    new_instruction = "Use the newly published instruction."
    factory = _GoalAwareFactory(
        _TurnStep("complete", gate=completion_gate),
    )
    application, automation = _application(
        tmp_path,
        factory,
        prompt=old_instruction,
    )
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="revision-pinning",
        )
        _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.RUNNING,
        )
        updated = application.automations.update(
            automation.id,
            prompt=new_instruction,
        )

        with application.database.read() as connection:
            revisions = AutomationRevisionRepository(connection).list_for_automation(
                automation.id
            )
            persisted_run = AutomationRunRepository(connection).get(execution.run.id)
            turns = TurnRepository(connection).list_for_thread(automation.thread_id)

        assert persisted_run is not None
        assert updated.current_revision_id != automation.current_revision_id
        assert persisted_run.revision_id == automation.current_revision_id
        assert [revision.ordinal for revision in revisions] == [1, 2]
        assert [revision.instruction for revision in revisions] == [
            old_instruction,
            new_instruction,
        ]
        assert updated.current_revision_id == revisions[1].id
        assert persisted_run.revision_id == revisions[0].id
        assert [turn.prompt for turn in turns] == [old_instruction]

        completion_gate.set()
        _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )
    finally:
        completion_gate.set()
        application.close()


def test_new_occurrence_during_active_run_is_terminal_skipped_without_turn(
    tmp_path: Path,
) -> None:
    completion_gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep("complete", gate=completion_gate),
    )
    application, automation = _application(tmp_path, factory)
    try:
        active = application.automations.run_now(
            automation.id,
            request_id="active-occurrence",
        )
        _wait_for_run(
            application,
            automation.id,
            active.run.id,
            AutomationRunStatus.RUNNING,
        )

        skipped = application.automations.run_now(
            automation.id,
            request_id="overlapping-occurrence",
        )

        assert skipped.run.id != active.run.id
        assert skipped.run.occurrence_id != active.run.occurrence_id
        assert skipped.run.status is AutomationRunStatus.SKIPPED
        assert skipped.run.status.is_terminal
        assert skipped.run.completed_at is not None
        assert skipped.turn is None
        assert "active" in skipped.run.detail.lower()
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            2,
            2,
            1,
        )
        assert len(factory.started_prompts) == 1

        completion_gate.set()
        _wait_for_run(
            application,
            automation.id,
            active.run.id,
            AutomationRunStatus.COMPLETED,
        )
    finally:
        completion_gate.set()
        application.close()


@pytest.mark.parametrize("mutation", ["clear", "replace"])
def test_goal_ownership_change_during_attributed_turn_keeps_run_open_until_settle(
    tmp_path: Path,
    mutation: str,
) -> None:
    turn_gate = threading.Event()
    factory = _GoalAwareFactory(_TurnStep(None, gate=turn_gate))
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id=f"ownership-{mutation}",
        )
        running = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.goal_id is not None
        assert running.turn_id is not None

        application.goals.clear(
            automation.thread_id,
            expected_goal_id=running.goal_id,
        )
        replacement_goal_id = None
        if mutation == "replace":
            replacement_goal_id = new_id("goal")
            application.goals.provision(
                automation.thread_id,
                goal_id=replacement_goal_id,
                objective="A user-owned replacement Goal",
            )

        # Reconciliation may observe the ownership change before the attributed
        # Turn settles. That must not release the one-open-run guard.
        application.automations.reconcile_runs()
        still_open = next(
            run for run in _runs(application, automation.id) if run.id == running.id
        )
        with application.database.read() as connection:
            guarded = AutomationRunRepository(connection).open_for_automation(
                automation.id
            )
        assert still_open.status is AutomationRunStatus.RUNNING
        assert still_open.completed_at is None
        assert guarded is not None
        assert guarded.id == running.id

        overlapping = application.automations.run_now(
            automation.id,
            request_id=f"ownership-{mutation}-overlap",
        )
        assert overlapping.run.status is AutomationRunStatus.SKIPPED
        assert overlapping.turn is None
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            2,
            2,
            1,
        )

        turn_gate.set()
        interrupted = _wait_for_run(
            application,
            automation.id,
            running.id,
            AutomationRunStatus.INTERRUPTED,
        )
        settled_turn = application.turns.read(running.turn_id).turn

        assert settled_turn.status is TurnStatus.COMPLETED
        assert interrupted.completed_at is not None
        assert interrupted.completed_at >= settled_turn.completed_at
        expected_detail = (
            "Automation Goal was cleared"
            if mutation == "clear"
            else f"Automation Goal was replaced by {replacement_goal_id}"
        )
        assert interrupted.detail == expected_detail
        with application.database.read() as connection:
            assert (
                AutomationRunRepository(connection).open_for_automation(automation.id)
                is None
            )
        current_goal = application.goals.read(automation.thread_id)
        if mutation == "clear":
            assert current_goal is None
        else:
            assert current_goal is not None
            assert current_goal.id == replacement_goal_id
        assert len(_turns(application, automation.thread_id)) == 1
        assert factory.decisions == []
    finally:
        turn_gate.set()
        application.close()


def test_retire_preserves_history_and_rejects_new_manual_occurrences(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="before-retire",
        )
        _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )
        counts_before = _fact_counts(
            application,
            automation.id,
            automation.thread_id,
        )

        assert application.automations.remove(automation.id) is True

        with application.database.read() as connection:
            retired = AutomationRepository(connection).get(
                automation.id,
                include_retired=True,
            )
            revisions = AutomationRevisionRepository(connection).list_for_automation(
                automation.id
            )
            runs = AutomationRunRepository(connection).list_for_automation(
                automation.id
            )
        assert retired is not None
        assert retired.status is AutomationStatus.RETIRED
        assert retired.next_run_at is None
        assert len(revisions) == 1
        assert [run.id for run in runs] == [execution.run.id]
        assert application.session_store.get_session(automation.thread_id) is not None

        with pytest.raises(AutomationNotFoundError):
            application.automations.run_now(
                automation.id,
                request_id="after-retire",
            )
        assert (
            _fact_counts(
                application,
                automation.id,
                automation.thread_id,
            )
            == counts_before
        )
    finally:
        application.close()


def test_agent_blocked_run_resumes_same_goal_and_same_run(tmp_path: Path) -> None:
    factory = _GoalAwareFactory(
        _TurnStep("blocked"),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="recoverable-blocker",
        )
        blocked = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.BLOCKED,
        )

        assert blocked.goal_id is not None
        assert blocked.completed_at is None
        goal = application.goals.read(automation.thread_id)
        assert goal is not None
        assert goal.id == blocked.goal_id
        assert goal.status is ThreadGoalStatus.BLOCKED
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )

        application.goals.resume(
            automation.thread_id,
            expected_goal_id=blocked.goal_id,
        )
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        assert completed.id == blocked.id
        assert completed.goal_id == blocked.goal_id
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            2,
        )
        turns = _turns(application, automation.thread_id)
        assert [turn.goal_id for turn in turns] == [
            blocked.goal_id,
            blocked.goal_id,
        ]
        assert factory.decisions == [(0, "blocked"), (1, "complete")]
    finally:
        application.close()


def test_interrupted_turn_keeps_run_open_for_explicit_goal_continue(
    tmp_path: Path,
) -> None:
    gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep(None, gate=gate),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="interrupt-and-continue",
        )
        running = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.turn_id is not None
        assert running.goal_id is not None

        accepted, interrupted_turn = application.turns.interrupt(
            automation.thread_id,
            running.turn_id,
        )
        assert accepted
        assert interrupted_turn.status is TurnStatus.INTERRUPTED
        blocked = _wait_for_run(
            application,
            automation.id,
            running.id,
            AutomationRunStatus.BLOCKED,
        )

        assert blocked.goal_id == running.goal_id
        assert blocked.completed_at is None
        assert "interrupted" in blocked.detail
        application.automations.reconcile_runs()
        application.automations.reconcile_runs()
        assert application.automations.list_runs(automation.id)[0] == blocked
        application.goals.continue_goal(
            automation.thread_id,
            expected_goal_id=blocked.goal_id,
        )
        completed = _wait_for_run(
            application,
            automation.id,
            blocked.id,
            AutomationRunStatus.COMPLETED,
        )

        assert completed.goal_id == blocked.goal_id
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            2,
        )
    finally:
        gate.set()
        application.close()


def test_interrupted_continuation_is_not_restarted_from_completed_initial_turn(
    tmp_path: Path,
) -> None:
    continuation_gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep(None),
        _TurnStep(None, gate=continuation_gate),
    )
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="interrupt-continuation",
        )
        turns = _wait_for_turn_count(application, automation.thread_id, 2)
        active = _wait_until(
            lambda: application.turns.active_for_thread(automation.thread_id),
            "the Goal continuation to become active",
        )
        assert active.id == turns[1].id

        accepted, interrupted = application.turns.interrupt(
            automation.thread_id,
            active.id,
        )
        assert accepted
        assert interrupted.status is TurnStatus.INTERRUPTED
        blocked = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.BLOCKED,
        )

        application.automations.reconcile_runs()
        application.automations.reconcile_runs()
        assert blocked.completed_at is None
        assert len(_turns(application, automation.thread_id)) == 2
        assert application.turns.active_for_thread(automation.thread_id) is None
    finally:
        continuation_gate.set()
        application.close()


@pytest.mark.parametrize("goal_preprovisioned", [False, True])
def test_reconcile_recovers_a_run_crash_window_without_duplicate_goal_or_turn(
    tmp_path: Path,
    goal_preprovisioned: bool,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    now = utc_now()
    occurrence = AutomationOccurrence(
        automation_id=automation.id,
        kind=AutomationTrigger.MANUAL,
        occurrence_key=f"crash-window-{goal_preprovisioned}",
        nominal_at=now,
        observed_at=now,
    )
    run = AutomationRun(
        automation_id=automation.id,
        revision_id=automation.current_revision_id,
        occurrence_id=occurrence.id,
        goal_id=new_id("goal"),
        thread_id=automation.thread_id,
        trigger=AutomationTrigger.MANUAL,
        status=AutomationRunStatus.QUEUED,
        scheduled_for=now,
        created_at=now,
        updated_at=now,
    )
    try:
        with application.database.transaction() as connection:
            AutomationOccurrenceRepository(connection).add(occurrence)
            AutomationRunRepository(connection).add(run)
        if goal_preprovisioned:
            application.goals.provision(
                automation.thread_id,
                goal_id=run.goal_id,
                objective=automation.prompt,
            )

        application.automations.reconcile_runs()
        recovered = _wait_for_run(
            application,
            automation.id,
            run.id,
            AutomationRunStatus.COMPLETED,
        )

        assert recovered.goal_id == run.goal_id
        assert recovered.turn_id is not None
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )
        goal_ledger = (
            application.session_store.root / automation.thread_id / "goal.jsonl"
        )
        created_entries = [
            line
            for line in goal_ledger.read_text(encoding="utf-8").splitlines()
            if '"reason":"provisioned"' in line
        ]
        assert len(created_entries) == 1
    finally:
        application.close()


def test_turn_transaction_failure_keeps_provisioned_goal_run_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    original_start = application.turns.start_with_participant
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected pre-commit failure")
        return original_start(*args, **kwargs)

    monkeypatch.setattr(
        application.turns,
        "start_with_participant",
        fail_once,
    )
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="pre-commit-failure",
        )

        assert execution.run.status is AutomationRunStatus.BLOCKED
        assert execution.run.goal_id is not None
        assert execution.run.turn_id is None
        assert execution.run.completed_at is None
        goal = application.goals.read(automation.thread_id)
        assert goal is not None
        assert goal.id == execution.run.goal_id
        assert goal.status is ThreadGoalStatus.ACTIVE
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            0,
        )

        application.automations.reconcile_runs()
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        assert completed.goal_id == execution.run.goal_id
        assert completed.turn_id is not None
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )
    finally:
        application.close()


@pytest.mark.parametrize(
    "intruder",
    ["goal_continue", "turn_start", "turn_enqueue"],
)
def test_pending_initial_turn_reservation_rejects_competing_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    intruder: str,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    original_start = application.turns.start_with_participant
    entered = threading.Event()
    release = threading.Event()

    def hold_initial_submission(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise AssertionError("timed out releasing Automation initial Turn")
        return original_start(*args, **kwargs)

    monkeypatch.setattr(
        application.turns,
        "start_with_participant",
        hold_initial_submission,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                application.automations.run_now,
                automation.id,
                request_id=f"reserved-{intruder}",
            )
            assert entered.wait(timeout=5)

            pending = _runs(application, automation.id)[0]
            assert pending.goal_id is not None
            assert pending.turn_id is None
            assert _turns(application, automation.thread_id) == ()

            with pytest.raises(ConflictError):
                if intruder == "goal_continue":
                    application.goals.continue_goal(
                        automation.thread_id,
                        expected_goal_id=pending.goal_id,
                    )
                elif intruder == "turn_start":
                    application.turns.start(
                        automation.thread_id,
                        prompt="Competing user Turn",
                    )
                else:
                    application.turns.enqueue(
                        automation.thread_id,
                        prompt="Competing queued Turn",
                    )

            assert _turns(application, automation.thread_id) == ()
            release.set()
            execution = future.result(timeout=10)

        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )
        turns = _turns(application, automation.thread_id)
        assert len(turns) == 1
        assert completed.turn_id == turns[0].id
        assert turns[0].prompt == automation.prompt
        initial_item = application.turns.read(turns[0].id).items[0]
        assert initial_item.payload["source"] == "automation"
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )
    finally:
        release.set()
        application.close()


def test_legacy_unreserved_turn_is_never_adopted_as_automation_initial_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep(None, gate=foreign_gate),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    original_start = application.turns.start_with_participant
    injected = False

    def inject_unreserved_turn(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            application.turns.start(
                automation.thread_id,
                prompt="A legacy client won the race",
            )
        return original_start(*args, **kwargs)

    # Simulate one old process that predates transaction-scoped admission. The
    # current reconciler must still fail closed instead of adopting its Turn.
    application.turns.remove_admission_guard(
        application.automations.ensure_turn_admitted
    )
    monkeypatch.setattr(
        application.turns,
        "start_with_participant",
        inject_unreserved_turn,
    )
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="legacy-unreserved-race",
        )
        application.turns.add_admission_guard(
            application.automations.ensure_turn_admitted
        )

        assert execution.run.status is AutomationRunStatus.BLOCKED
        assert execution.run.turn_id is None
        foreign = _turns(application, automation.thread_id)[0]
        assert foreign.prompt == "A legacy client won the race"
        assert foreign.id != execution.run.turn_id

        accepted, interrupted = application.turns.interrupt(
            automation.thread_id,
            foreign.id,
        )
        assert accepted
        assert interrupted.status is TurnStatus.INTERRUPTED
        application.automations.reconcile_runs()
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        turns = _turns(application, automation.thread_id)
        assert len(turns) == 2
        assert completed.turn_id == turns[1].id
        assert turns[1].prompt == automation.prompt
        assert factory.started_prompts == [
            "A legacy client won the race",
            automation.prompt,
        ]
    finally:
        foreign_gate.set()
        application.turns.add_admission_guard(
            application.automations.ensure_turn_admitted
        )
        application.close()


def test_goal_clear_waits_for_atomic_initial_turn_association(
    tmp_path: Path,
) -> None:
    turn_gate = threading.Event()
    factory = _GoalAwareFactory(_TurnStep(None, gate=turn_gate))
    application, automation = _application(tmp_path, factory)
    second = DeepCodeApplication.open(
        application.database.path,
        session_factory=_GoalAwareFactory(),
        session_store=SessionStore(
            application.session_store.root,
            use_index=False,
        ),
    )
    admission_entered = threading.Event()
    admission_release = threading.Event()
    clear_started = threading.Event()

    def hold_submission(_context) -> None:
        admission_entered.set()
        if not admission_release.wait(timeout=5):
            raise AssertionError("timed out releasing Turn admission")

    application.turns.add_admission_guard(hold_submission)
    try:

        def clear_goal(goal_id: str) -> None:
            clear_started.set()
            second.goals.clear(
                automation.thread_id,
                expected_goal_id=goal_id,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            run_future = pool.submit(
                application.automations.run_now,
                automation.id,
                request_id="clear-during-atomic-association",
            )
            assert admission_entered.wait(timeout=5)
            pending = _runs(application, automation.id)[0]
            assert pending.goal_id is not None

            clear_future = pool.submit(
                clear_goal,
                pending.goal_id,
            )
            assert clear_started.wait(timeout=5)
            assert not clear_future.done()

            admission_release.set()
            execution = run_future.result(timeout=10)
            clear_future.result(timeout=10)

        assert execution.run.turn_id is not None
        turn_gate.set()
        interrupted = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.INTERRUPTED,
        )
        assert interrupted.detail == "Automation Goal was cleared"
        assert len(_turns(application, automation.thread_id)) == 1
    finally:
        admission_release.set()
        turn_gate.set()
        application.turns.remove_admission_guard(hold_submission)
        second.close()
        application.close()


@pytest.mark.parametrize("mutation", ["clear", "replace"])
def test_goal_continuation_rejects_cross_app_goal_change_before_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    initial_gate = threading.Event()
    factory = _GoalAwareFactory(_TurnStep(None, gate=initial_gate))
    application, automation = _application(tmp_path, factory)
    second = DeepCodeApplication.open(
        application.database.path,
        session_factory=_GoalAwareFactory(),
        session_store=SessionStore(
            application.session_store.root,
            use_index=False,
        ),
    )
    entered = threading.Event()
    release = threading.Event()
    original_start = application.turns.start

    def hold_after_continuation_checks(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise AssertionError("timed out releasing Goal continuation")
        return original_start(*args, **kwargs)

    try:
        execution = application.automations.run_now(
            automation.id,
            request_id=f"continuation-goal-change-{mutation}",
        )
        running = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.turn_id is not None
        assert running.goal_id is not None
        accepted, interrupted = application.turns.interrupt(
            automation.thread_id,
            running.turn_id,
        )
        assert accepted
        assert interrupted.status is TurnStatus.INTERRUPTED
        _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.BLOCKED,
        )

        monkeypatch.setattr(application.turns, "start", hold_after_continuation_checks)
        with ThreadPoolExecutor(max_workers=1) as pool:
            continuation = pool.submit(
                application.goals.continue_goal,
                automation.thread_id,
                expected_goal_id=running.goal_id,
            )
            assert entered.wait(timeout=5)
            second.goals.clear(
                automation.thread_id,
                expected_goal_id=running.goal_id,
            )
            replacement_goal_id = None
            if mutation == "replace":
                replacement_goal_id = new_id("goal")
                second.goals.provision(
                    automation.thread_id,
                    goal_id=replacement_goal_id,
                    objective="Replacement Goal",
                )
            release.set()
            with pytest.raises(ConflictError, match="association changed"):
                continuation.result(timeout=10)

        assert len(_turns(application, automation.thread_id)) == 1
        current_goal = second.goals.read(automation.thread_id)
        if mutation == "clear":
            assert current_goal is None
        else:
            assert current_goal is not None
            assert current_goal.id == replacement_goal_id
        application.automations.reconcile_runs()
        settled = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.INTERRUPTED,
        )
        assert settled.turn_id == running.turn_id
    finally:
        release.set()
        initial_gate.set()
        second.close()
        application.close()


def test_stale_cross_app_resume_cannot_reopen_a_completed_automation_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion_gate = threading.Event()
    application, automation = _application(tmp_path, _GoalAwareFactory())
    second_factory = _GoalAwareFactory(
        _TurnStep("complete", gate=completion_gate),
    )
    second = DeepCodeApplication.open(
        application.database.path,
        session_factory=second_factory,
        session_store=SessionStore(
            application.session_store.root,
            use_index=False,
        ),
    )
    stale_resume_entered = threading.Event()
    stale_resume_release = threading.Event()
    original_update = application.goals.store.update

    def hold_stale_resume(*args, **kwargs):
        stale_resume_entered.set()
        if not stale_resume_release.wait(timeout=5):
            raise AssertionError("timed out releasing stale Goal resume")
        return original_update(*args, **kwargs)

    try:
        execution = second.automations.run_now(
            automation.id,
            request_id="stale-cross-app-resume",
        )
        running = _wait_for_run(
            second,
            automation.id,
            execution.run.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.goal_id is not None
        application.goals.pause(
            automation.thread_id,
            expected_goal_id=running.goal_id,
        )

        monkeypatch.setattr(application.goals.store, "update", hold_stale_resume)
        with ThreadPoolExecutor(max_workers=1) as pool:
            stale_resume = pool.submit(
                application.goals.resume,
                automation.thread_id,
                expected_goal_id=running.goal_id,
            )
            assert stale_resume_entered.wait(timeout=5)

            second.goals.resume(
                automation.thread_id,
                expected_goal_id=running.goal_id,
            )
            completion_gate.set()
            _wait_for_run(
                second,
                automation.id,
                execution.run.id,
                AutomationRunStatus.COMPLETED,
            )
            completed_goal = _wait_until(
                lambda: (
                    goal
                    if (goal := second.goals.read(automation.thread_id)) is not None
                    and goal.status is ThreadGoalStatus.COMPLETE
                    else None
                ),
                "Automation Goal to complete before stale resume",
            )

            stale_resume_release.set()
            with pytest.raises(ConflictError):
                stale_resume.result(timeout=10)

        final_goal = application.goals.read(automation.thread_id)
        assert final_goal is not None
        assert final_goal.id == completed_goal.id
        assert final_goal.status is ThreadGoalStatus.COMPLETE
        assert len(_turns(application, automation.thread_id)) == 1
        persisted = next(
            run
            for run in _runs(application, automation.id)
            if run.id == execution.run.id
        )
        assert persisted.status is AutomationRunStatus.COMPLETED
    finally:
        completion_gate.set()
        stale_resume_release.set()
        second.close()
        application.close()


def test_restart_does_not_reprovision_a_cleared_pending_automation_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    database_path = application.database.path
    session_store = application.session_store

    def fail_before_commit(*_args, **_kwargs):
        raise RuntimeError("injected pre-commit failure")

    monkeypatch.setattr(
        application.turns,
        "start_with_participant",
        fail_before_commit,
    )
    restarted = None
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="clear-before-restart",
        )
        assert execution.run.goal_id is not None
        assert execution.run.turn_id is None
        application.goals.clear(
            automation.thread_id,
            expected_goal_id=execution.run.goal_id,
        )
        application.close()

        restarted = DeepCodeApplication.open(
            database_path,
            session_factory=factory,
            session_store=session_store,
        )
        persisted = next(
            run
            for run in restarted.automations.list_runs(automation.id)
            if run.id == execution.run.id
        )

        assert persisted.status is AutomationRunStatus.INTERRUPTED
        assert persisted.turn_id is None
        assert persisted.completed_at is not None
        assert restarted.goals.read(automation.thread_id) is None
        assert _fact_counts(
            restarted,
            automation.id,
            automation.thread_id,
        ) == (1, 1, 0)
        with restarted.database.read() as connection:
            assert (
                AutomationRunRepository(connection).open_for_automation(automation.id)
                is None
            )
        goal_ledger = session_store.root / automation.thread_id / "goal.jsonl"
        provisioned = [
            line
            for line in goal_ledger.read_text(encoding="utf-8").splitlines()
            if '"reason":"provisioned"' in line
        ]
        assert len(provisioned) == 1
    finally:
        if restarted is not None:
            restarted.close()
        else:
            application.close()


def test_automation_guards_do_not_restrict_an_unowned_goal(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    try:
        thread = application.threads.start(
            automation.project_id,
            title="Ordinary Goal",
        )
        goal = application.goals.create(
            thread.id,
            objective="Complete an ordinary user Goal.",
            start=False,
        )

        continued = application.goals.continue_goal(
            thread.id,
            expected_goal_id=goal.id,
        )

        assert continued.disposition is GoalContinueDisposition.STARTED
        terminal = _wait_until(
            lambda: (
                turn
                if (
                    turn := application.turns.read(continued.turn_id).turn
                ).status.is_terminal
                else None
            ),
            "ordinary Goal Turn to settle",
        )
        assert terminal.status is TurnStatus.COMPLETED
        current = application.goals.read(thread.id)
        assert current is not None
        assert current.status is ThreadGoalStatus.COMPLETE
        assert _runs(application, automation.id) == ()
        with application.database.read() as connection:
            assert AutomationRunRepository(connection).get_for_goal(goal.id) is None
    finally:
        application.close()


def test_live_run_recovers_after_one_goal_settlement_listener_failure(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(
        _TurnStep(None),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    goal_listener = application.goals.on_turn_settled
    automation_listener = application.automations.on_turn_settled
    application.turns.remove_settled_listener(goal_listener)
    application.turns.remove_settled_listener(automation_listener)
    calls = 0

    def flaky_goal_listener(turn: Turn) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected Goal settlement failure")
        goal_listener(turn)

    application.turns.add_settled_listener(flaky_goal_listener)
    application.turns.add_settled_listener(automation_listener)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="live-goal-settlement-retry",
        )
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        turns = _turns(application, automation.thread_id)
        assert len(turns) == 2
        assert calls == 2
        assert completed.goal_id is not None
        assert all(turn.goal_id == completed.goal_id for turn in turns)
        assert application.goals.are_turns_accounted(
            automation.thread_id,
            goal_id=completed.goal_id,
            turn_ids=tuple(turn.id for turn in turns),
        )
    finally:
        application.turns.remove_settled_listener(flaky_goal_listener)
        application.turns.remove_settled_listener(automation_listener)
        application.turns.add_settled_listener(goal_listener)
        application.turns.add_settled_listener(automation_listener)
        application.close()


def test_application_restart_replays_missing_goal_settlement_before_run_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _GoalAwareFactory(
        _TurnStep(None),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    database_path = application.database.path
    session_store = application.session_store
    restarted = None
    try:
        # Simulate a process dying after the terminal Turn commit but before
        # the first post-commit Goal listener ran.
        application.turns.remove_settled_listener(application.goals.on_turn_settled)
        monkeypatch.setattr(
            application.goals,
            "reconcile_turn_settlements",
            lambda *_args, **_kwargs: 0,
        )
        execution = application.automations.run_now(
            automation.id,
            request_id="missing-goal-settlement",
        )
        waiting = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.WAITING,
        )
        assert waiting.goal_id is not None
        assert waiting.turn_id is not None
        assert not application.goals.is_turn_accounted(
            automation.thread_id,
            goal_id=waiting.goal_id,
            turn_id=waiting.turn_id,
        )
        application.close()

        restarted = DeepCodeApplication.open(
            database_path,
            session_factory=factory,
            session_store=session_store,
        )
        completed = _wait_for_run(
            restarted,
            automation.id,
            waiting.id,
            AutomationRunStatus.COMPLETED,
        )

        assert completed.goal_id == waiting.goal_id
        assert completed.turn_id == waiting.turn_id
        assert _fact_counts(
            restarted,
            automation.id,
            automation.thread_id,
        ) == (1, 1, 2)
        turns = _turns(restarted, automation.thread_id)
        assert all(turn.goal_id == waiting.goal_id for turn in turns)
        assert restarted.goals.is_turn_accounted(
            automation.thread_id,
            goal_id=waiting.goal_id,
            turn_id=waiting.turn_id,
        )
    finally:
        if restarted is not None:
            restarted.close()
        else:
            application.close()


def test_run_waits_for_every_continuation_settlement_before_goal_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion_gate = threading.Event()
    factory = _GoalAwareFactory(
        _TurnStep(None),
        _TurnStep("complete", gate=completion_gate),
    )
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="continuation-settlement",
        )
        turns = _wait_for_turn_count(application, automation.thread_id, 2)
        _wait_until(
            lambda: (
                current
                if (current := application.turns.read(turns[1].id).turn).status
                is TurnStatus.RUNNING
                else None
            ),
            "the deciding continuation to become active",
        )
        application.turns.remove_settled_listener(application.goals.on_turn_settled)
        monkeypatch.setattr(
            application.goals,
            "reconcile_turn_settlements",
            lambda *_args, **_kwargs: 0,
        )
        completion_gate.set()

        waiting = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.WAITING,
        )
        settled_turns = _turns(application, automation.thread_id)
        assert len(settled_turns) == 2
        goal = application.goals.read(automation.thread_id)
        assert goal is not None
        assert goal.status is ThreadGoalStatus.COMPLETE
        assert application.goals.is_turn_accounted(
            automation.thread_id,
            goal_id=goal.id,
            turn_id=settled_turns[0].id,
        )
        assert not application.goals.is_turn_accounted(
            automation.thread_id,
            goal_id=goal.id,
            turn_id=settled_turns[1].id,
        )

        application.goals.on_turn_settled(settled_turns[1])
        application.automations.reconcile_runs()
        completed = _wait_for_run(
            application,
            automation.id,
            waiting.id,
            AutomationRunStatus.COMPLETED,
        )
        assert completed.goal_id == goal.id
    finally:
        completion_gate.set()
        application.close()


def test_ordinary_turn_cannot_bypass_pending_automation_goal_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _GoalAwareFactory(_TurnStep(None))
    application, automation = _application(tmp_path, factory)
    application.turns.remove_settled_listener(application.goals.on_turn_settled)
    monkeypatch.setattr(
        application.goals,
        "reconcile_turn_settlements",
        lambda *_args, **_kwargs: 0,
    )
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="ordinary-turn-settlement-bypass",
        )
        _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.WAITING,
        )

        with pytest.raises(ConflictError, match="settlement is incomplete"):
            application.turns.start(
                automation.thread_id,
                prompt="Bypass the missing Goal receipt",
            )

        assert len(_turns(application, automation.thread_id)) == 1
    finally:
        application.close()


def test_terminal_automation_goal_rejects_turn_after_legacy_stale_reopen(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(_TurnStep("complete"))
    application, automation = _application(tmp_path, factory)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="terminal-goal-admission",
        )
        completed = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.COMPLETED,
        )
        assert completed.goal_id is not None

        # Simulate state written by a pre-fix process. The Turn admission seam
        # must still fail closed when durable Run ownership is terminal.
        application.goals.store.update(
            automation.thread_id,
            expected_goal_id=completed.goal_id,
            transform=lambda goal: goal.user_transition(ThreadGoalStatus.ACTIVE),
            reason="legacy stale resume",
            source="user",
        )

        with pytest.raises(ConflictError, match="settled Automation Goal"):
            application.turns.start(
                automation.thread_id,
                prompt="Do not create an orphan Turn",
            )

        assert len(_turns(application, automation.thread_id)) == 1
    finally:
        application.close()


def test_settled_automation_goal_cannot_be_reopened_into_an_orphan_turn(
    tmp_path: Path,
) -> None:
    factory = _GoalAwareFactory(
        _TurnStep("complete"),
        _TurnStep("complete"),
    )
    application, automation = _application(tmp_path, factory)
    try:
        first_execution = application.automations.run_now(
            automation.id,
            request_id="first-owned-goal",
        )
        first = _wait_for_run(
            application,
            automation.id,
            first_execution.run.id,
            AutomationRunStatus.COMPLETED,
        )
        assert first.goal_id is not None

        with pytest.raises(ConflictError, match="Automation Goal"):
            application.goals.resume(
                automation.thread_id,
                expected_goal_id=first.goal_id,
            )

        assert len(_turns(application, automation.thread_id)) == 1
        current_goal = application.goals.read(automation.thread_id)
        assert current_goal is not None
        assert current_goal.id == first.goal_id
        assert current_goal.status is ThreadGoalStatus.COMPLETE

        second_execution = application.automations.run_now(
            automation.id,
            request_id="second-owned-goal",
        )
        second = _wait_for_run(
            application,
            automation.id,
            second_execution.run.id,
            AutomationRunStatus.COMPLETED,
        )

        assert second.id != first.id
        assert second.goal_id != first.goal_id
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            2,
            2,
            2,
        )
    finally:
        application.close()


def test_scheduler_failure_keeps_one_linked_run_and_blocks_its_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _GoalAwareFactory()
    application, automation = _application(tmp_path, factory)

    def fail_schedule(*_args, **_kwargs):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(application.executions, "start", fail_schedule)
    try:
        execution = application.automations.run_now(
            automation.id,
            request_id="scheduler-failure",
        )
        blocked = _wait_for_run(
            application,
            automation.id,
            execution.run.id,
            AutomationRunStatus.BLOCKED,
        )

        assert blocked.turn_id is not None
        assert blocked.goal_id is not None
        assert _fact_counts(application, automation.id, automation.thread_id) == (
            1,
            1,
            1,
        )
        turn = application.turns.read(blocked.turn_id).turn
        assert turn.status is TurnStatus.FAILED
        assert turn.error_code == "SCHEDULER_ERROR"
        goal = application.goals.read(automation.thread_id)
        assert goal is not None
        assert goal.id == blocked.goal_id
        assert goal.status is ThreadGoalStatus.BLOCKED
        assert factory.sessions == []
    finally:
        application.close()


def test_concurrent_clients_with_one_request_id_converge_on_one_run(
    tmp_path: Path,
) -> None:
    gate = threading.Event()
    first_factory = _GoalAwareFactory(_TurnStep("complete", gate=gate))
    second_factory = _GoalAwareFactory(_TurnStep("complete", gate=gate))
    first, automation = _application(tmp_path, first_factory)
    second = DeepCodeApplication.open(
        first.database.path,
        session_factory=second_factory,
        session_store=first.session_store,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    application.automations.run_now,
                    automation.id,
                    request_id="shared-retry-token",
                )
                for application in (first, second)
            ]
            executions = [future.result(timeout=10) for future in futures]

        assert executions[0].run.id == executions[1].run.id
        assert executions[0].run.occurrence_id == executions[1].run.occurrence_id
        assert _fact_counts(first, automation.id, automation.thread_id) == (1, 1, 1)
        _wait_until(
            lambda: len(first_factory.sessions) + len(second_factory.sessions) == 1,
            "the single admitted Automation Turn to enter its Agent runtime",
        )

        gate.set()
        _wait_for_run(
            first,
            automation.id,
            executions[0].run.id,
            AutomationRunStatus.COMPLETED,
        )
    finally:
        gate.set()
        second.close()
        first.close()


def test_concurrent_schedulers_claim_one_due_occurrence_and_one_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gate = threading.Event()
    first_factory = _GoalAwareFactory(_TurnStep("complete", gate=gate))
    second_factory = _GoalAwareFactory(_TurnStep("complete", gate=gate))
    first = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=first_factory,
    )
    project = first.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    created = first.automations.create(
        project_id=project.id,
        name="Scheduled caretaker",
        prompt="Inspect and maintain the repository.",
        schedule_kind=AutomationScheduleKind.INTERVAL,
        interval_seconds=60,
    )
    due = created.automation.next_run_at
    assert due is not None
    second = DeepCodeApplication.open(
        first.database.path,
        session_factory=second_factory,
        session_store=first.session_store,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(application.automations.run_due, due)
                for application in (first, second)
            ]
            batches = [future.result(timeout=10) for future in futures]

        claimed = tuple(run for batch in batches for run in batch)
        assert claimed
        assert len({run.id for run in claimed}) == 1
        assert _fact_counts(
            first,
            created.automation.id,
            created.automation.thread_id,
        ) == (1, 1, 1)
        _wait_until(
            lambda: len(first_factory.sessions) + len(second_factory.sessions) == 1,
            "one scheduler-owned Agent Session",
        )

        gate.set()
        _wait_for_run(
            first,
            created.automation.id,
            claimed[0].id,
            AutomationRunStatus.COMPLETED,
        )
    finally:
        gate.set()
        second.close()
        first.close()
