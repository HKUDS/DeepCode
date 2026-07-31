from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

import pytest

from core.agent_runtime.goal_runtime import GoalRuntimeContext
from core.application.errors import ConflictError
from core.application.goal_extension import (
    GoalContinueDisposition,
    GoalExtension,
)
from core.domain.common import utc_now
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import ClientSurface, TurnInputSource
from core.domain.thread_goal import ThreadGoalStatus
from core.domain.turn import Turn, TurnStatus
from core.sessions import SessionStore
from core.sessions.thread_goal_store import ThreadGoalStore


@dataclass(frozen=True)
class _Snapshot:
    turn: Turn
    items: tuple[Item, ...] = ()


class _Turns:
    def __init__(self) -> None:
        self.active: Turn | None = None
        self.snapshots: dict[str, _Snapshot] = {}
        self.started: list[str] = []
        self.start_options: list[dict] = []
        self.injected: list[tuple[str, str, str]] = []

    def start(self, thread_id: str, *, prompt: str, **kwargs) -> _Snapshot:
        self.started.append(prompt)
        self.start_options.append(kwargs)
        turn = Turn(thread_id=thread_id, ordinal=len(self.started), prompt=prompt)
        self.active = turn
        snapshot = _Snapshot(turn)
        self.snapshots[turn.id] = snapshot
        return snapshot

    def read(self, turn_id: str) -> _Snapshot:
        return self.snapshots[turn_id]

    def active_for_thread(self, _thread_id: str) -> Turn | None:
        return self.active

    def executing_for_thread(self, _thread_id: str) -> Turn | None:
        if self.active is not None and self.active.status in {
            TurnStatus.RUNNING,
            TurnStatus.WAITING_APPROVAL,
        }:
            return self.active
        return None

    def list_for_goal(self, thread_id: str, goal_id: str) -> tuple[Turn, ...]:
        return tuple(
            snapshot.turn
            for snapshot in sorted(
                self.snapshots.values(),
                key=lambda candidate: candidate.turn.ordinal,
            )
            if snapshot.turn.thread_id == thread_id and snapshot.turn.goal_id == goal_id
        )

    def inject_goal_update(
        self,
        turn_id: str,
        *,
        message_id: str,
        goal_id: str,
        objective: str,
    ) -> bool:
        self.injected.append((turn_id, goal_id, objective))
        return True


def _extension(tmp_path) -> tuple[GoalExtension, _Turns, str]:
    sessions = SessionStore(tmp_path / "sessions", use_index=False)
    session = sessions.create_session(title="Goal extension test")
    turns = _Turns()
    return GoalExtension(ThreadGoalStore(sessions), turns), turns, session.session_id


def _terminal_turn(
    *,
    thread_id: str,
    goal_id: str,
    status: TurnStatus,
    error_message: str | None = None,
) -> tuple[Turn, Item]:
    started = utc_now()
    turn = Turn(
        thread_id=thread_id,
        ordinal=1,
        prompt="work",
        goal_id=goal_id,
        status=status,
        error_code="AGENT_EXECUTION_ERROR" if status is TurnStatus.FAILED else None,
        error_message=error_message if status is TurnStatus.FAILED else None,
        started_at=started,
        completed_at=started + timedelta(seconds=3),
    )
    completion = Item(
        thread_id=thread_id,
        turn_id=turn.id,
        ordinal=1,
        kind=ItemKind.COMPLETION,
        status=(
            ItemStatus.COMPLETED
            if status is TurnStatus.COMPLETED
            else ItemStatus.FAILED
        ),
        summary="settled",
        payload={"usage": {"total_tokens": 42}},
    )
    return turn, completion


def test_active_goal_is_attributed_to_every_ordinary_turn(tmp_path) -> None:
    goals, _, session_id = _extension(tmp_path)
    created = goals.create(
        session_id,
        objective="Ship",
        skill_ids=("sk_0123456789abcdef01234567",),
        start=False,
    )

    association = goals.turn_association(session_id)

    assert association is not None
    assert association.goal_id == created.id
    assert association.skill_ids == created.skill_ids


def test_provision_is_idempotent_across_process_restart_without_duplicate_write(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    goal_id = "goal_000000000000000000000001"
    created = goals.provision(
        session_id,
        goal_id=goal_id,
        objective=" Keep the repository healthy ",
        token_budget=500,
        skill_ids=("sk_0123456789abcdef01234567",),
    )
    ledger = tmp_path / "sessions" / session_id / "goal.jsonl"
    committed = ledger.read_bytes()

    restarted = GoalExtension(
        ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False)),
        _Turns(),
    )
    retried = restarted.provision(
        session_id,
        goal_id=goal_id,
        objective="Keep the repository healthy",
        token_budget=500,
        skill_ids=("sk_0123456789abcdef01234567",),
    )

    assert retried == created
    assert retried.id == goal_id
    assert ledger.read_bytes() == committed
    assert not turns.started


def test_concurrent_provision_retries_converge_on_one_ledger_entry(tmp_path) -> None:
    first, _turns, session_id = _extension(tmp_path)
    second = GoalExtension(
        ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False)),
        _Turns(),
    )
    goal_id = "goal_000000000000000000000001"

    def provision(goals: GoalExtension):
        return goals.provision(
            session_id,
            goal_id=goal_id,
            objective="Ship",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(provision, (first, second)))

    ledger = tmp_path / "sessions" / session_id / "goal.jsonl"
    assert results[0] == results[1]
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_provision_retry_returns_advanced_runtime_state_without_writing(
    tmp_path,
) -> None:
    goals, _turns, session_id = _extension(tmp_path)
    goal_id = "goal_000000000000000000000001"
    goals.provision(session_id, goal_id=goal_id, objective="Ship")
    paused = goals.pause(session_id, expected_goal_id=goal_id)
    ledger = tmp_path / "sessions" / session_id / "goal.jsonl"
    committed = ledger.read_bytes()

    retried = goals.provision(session_id, goal_id=goal_id, objective="Ship")

    assert retried == paused
    assert retried.status is ThreadGoalStatus.PAUSED
    assert ledger.read_bytes() == committed


def test_provision_rejects_a_different_existing_goal_identity(tmp_path) -> None:
    goals, _turns, session_id = _extension(tmp_path)
    goals.provision(
        session_id,
        goal_id="goal_000000000000000000000001",
        objective="Ship",
    )

    with pytest.raises(ConflictError, match="different Goal"):
        goals.provision(
            session_id,
            goal_id="goal_000000000000000000000002",
            objective="Ship",
        )


@pytest.mark.parametrize(
    ("objective", "token_budget", "skill_ids"),
    [
        ("Ship a different objective", 500, ("sk_0123456789abcdef01234567",)),
        ("Ship", 700, ("sk_0123456789abcdef01234567",)),
        ("Ship", 500, ("sk_89abcdef0123456701234567",)),
    ],
)
def test_provision_rejects_changed_declarative_content(
    tmp_path,
    objective,
    token_budget,
    skill_ids,
) -> None:
    goals, _turns, session_id = _extension(tmp_path)
    goal_id = "goal_000000000000000000000001"
    goals.provision(
        session_id,
        goal_id=goal_id,
        objective="Ship",
        token_budget=500,
        skill_ids=("sk_0123456789abcdef01234567",),
    )

    with pytest.raises(ConflictError, match="provisioning content changed"):
        goals.provision(
            session_id,
            goal_id=goal_id,
            objective=objective,
            token_budget=token_budget,
            skill_ids=skill_ids,
        )


def test_regular_create_remains_non_idempotent_and_starts_work(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)

    created = goals.create(session_id, objective="Ship")

    assert created.id
    assert len(turns.started) == 1
    with pytest.raises(ConflictError, match="already has a Goal"):
        goals.create(session_id, objective="Ship")


def test_explicit_continue_starts_once_and_is_idempotent_while_active(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)

    started = goals.continue_goal(
        session_id,
        expected_goal_id=created.id,
    )
    repeated = goals.continue_goal(
        session_id,
        expected_goal_id=created.id,
    )

    assert started.goal.id == created.id
    assert started.goal.thread_id == session_id
    assert started.goal.objective == created.objective
    assert started.disposition is GoalContinueDisposition.STARTED
    assert started.turn_id == turns.active.id
    assert repeated.goal.id == created.id
    assert repeated.goal.objective == created.objective
    assert repeated.disposition is GoalContinueDisposition.ALREADY_RUNNING
    assert repeated.turn_id == started.turn_id
    assert len(turns.started) == 1


def test_explicit_continue_rejects_stale_goal_identity(tmp_path) -> None:
    goals, _turns, session_id = _extension(tmp_path)
    goals.create(session_id, objective="Ship", start=False)

    with pytest.raises(ConflictError, match="Goal identity changed"):
        goals.continue_goal(
            session_id,
            expected_goal_id="goal_000000000000000000000000",
        )


def test_explicit_goal_continuation_preserves_requesting_surface(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)

    goals.continue_goal(
        session_id,
        expected_goal_id=created.id,
        client_surface=ClientSurface.HEADLESS,
        connection_id="router",
        model="model-next",
        reasoning_effort="high",
    )

    assert turns.start_options[-1]["client_surface"] is ClientSurface.HEADLESS
    assert turns.start_options[-1]["input_source"] is TurnInputSource.GOAL_CONTINUATION
    assert turns.start_options[-1]["connection_id"] == "router"
    assert turns.start_options[-1]["model"] == "model-next"
    assert turns.start_options[-1]["reasoning_effort"] == "high"


def test_active_edit_keeps_identity_and_injects_current_turn(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="First", start=False)
    active = Turn(
        thread_id=session_id,
        ordinal=1,
        prompt="work",
        goal_id=created.id,
        status=TurnStatus.RUNNING,
        started_at=utc_now(),
    )
    turns.active = active
    turns.snapshots[active.id] = _Snapshot(active)

    edited = goals.edit(
        session_id,
        expected_goal_id=created.id,
        objective="Corrected objective",
        token_budget=None,
        skill_ids=(),
    )

    assert edited.id == created.id
    assert turns.injected == [(active.id, created.id, "Corrected objective")]
    assert not turns.started


def test_agent_tool_uses_runtime_identity_and_updates_directly(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    active = Turn(
        thread_id=session_id,
        ordinal=1,
        prompt="work",
        goal_id=created.id,
        status=TurnStatus.RUNNING,
        started_at=utc_now(),
    )
    turns.active = active
    turns.snapshots[active.id] = _Snapshot(active)

    result = goals.update_goal(
        GoalRuntimeContext(
            thread_id=session_id,
            goal_id=created.id,
            turn_id=active.id,
        ),
        status="complete",
        reason="The requested implementation and tests are complete.",
    )

    assert result["updated"] is True
    assert result["goal"]["status"] == "complete"
    assert goals.read(session_id).status is ThreadGoalStatus.COMPLETE


def test_outcome_projects_only_bounded_existing_items_from_deciding_turn(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    active = Turn(
        thread_id=session_id,
        ordinal=1,
        prompt="work",
        goal_id=created.id,
        status=TurnStatus.RUNNING,
        started_at=utc_now(),
    )
    supported = tuple(
        Item(
            thread_id=session_id,
            turn_id=active.id,
            ordinal=ordinal,
            kind=(ItemKind.TEST_RESULT if ordinal % 2 else ItemKind.COMMAND_EXECUTION),
            status=ItemStatus.COMPLETED,
            summary=("verified " + ("x" * 300)) if ordinal == 1 else f"step {ordinal}",
            payload={},
        )
        for ordinal in range(1, 15)
    )
    unsupported = Item(
        thread_id=session_id,
        turn_id=active.id,
        ordinal=15,
        kind=ItemKind.ASSISTANT_MESSAGE,
        status=ItemStatus.COMPLETED,
        summary="This narrative is not an evidence reference.",
        payload={"text": "done"},
    )
    turns.active = active
    turns.snapshots[active.id] = _Snapshot(active, (*supported, unsupported))

    goals.update_goal(
        GoalRuntimeContext(
            thread_id=session_id,
            goal_id=created.id,
            turn_id=active.id,
        ),
        status="complete",
        reason="The implementation and focused tests are complete.",
    )
    outcome = goals.read_outcome(session_id)

    assert outcome is not None
    assert outcome.status is ThreadGoalStatus.COMPLETE
    assert outcome.reason == "The implementation and focused tests are complete."
    assert outcome.decided_by_turn_id == active.id
    assert len(outcome.evidence_refs) == 12
    assert {evidence.turn_id for evidence in outcome.evidence_refs} == {active.id}
    assert {evidence.kind for evidence in outcome.evidence_refs} <= {
        "test_result",
        "command_execution",
    }
    assert all(len(evidence.summary) <= 240 for evidence in outcome.evidence_refs)


def test_only_normal_completion_starts_a_continuation(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    interrupted, interrupted_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.INTERRUPTED,
    )
    turns.snapshots[interrupted.id] = _Snapshot(
        interrupted,
        (interrupted_item,),
    )

    goals.on_turn_settled(interrupted)

    after_interrupt = goals.read(session_id)
    assert after_interrupt is not None
    assert after_interrupt.status is ThreadGoalStatus.ACTIVE
    assert after_interrupt.tokens_used == 42
    assert not turns.started

    completed, completed_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    turns.snapshots[completed.id] = _Snapshot(completed, (completed_item,))
    goals.on_turn_settled(completed)

    assert len(turns.started) == 1
    assert "Ship" in turns.started[0]


def test_terminal_turn_error_blocks_without_retry_loop(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    failed, completion = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.FAILED,
        error_message="provider exhausted retries",
    )
    turns.snapshots[failed.id] = _Snapshot(failed, (completion,))

    goals.on_turn_settled(failed)

    current = goals.read(session_id)
    assert current is not None
    assert current.status is ThreadGoalStatus.BLOCKED
    outcome = goals.read_outcome(session_id)
    assert outcome is not None
    assert outcome.reason == "provider exhausted retries"
    assert outcome.decided_by_turn_id == failed.id
    assert not turns.started


def test_terminal_goal_turn_settlement_is_idempotent_across_recovery(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    interrupted, completion = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.INTERRUPTED,
    )
    turns.snapshots[interrupted.id] = _Snapshot(interrupted, (completion,))

    goals.on_turn_settled(interrupted)
    goals.on_turn_settled(interrupted)
    recovered = goals.recover_incomplete()

    current = goals.read(session_id)
    assert current is not None
    assert current.tokens_used == 42
    assert current.time_used_seconds == 3
    assert recovered == 0
    assert not turns.started


def test_exact_goal_reconciliation_accounts_idempotently_and_continues_opt_in(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    completed, completion = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    turns.snapshots[completed.id] = _Snapshot(completed, (completion,))

    assert (
        goals.reconcile_turn_settlements(
            session_id,
            expected_goal_id="goal_000000000000000000000001",
        )
        == 0
    )
    assert not goals.is_turn_accounted(
        session_id,
        goal_id=created.id,
        turn_id=completed.id,
    )

    assert (
        goals.reconcile_turn_settlements(
            session_id,
            expected_goal_id=created.id,
        )
        == 1
    )
    assert not turns.started
    assert (
        goals.reconcile_turn_settlements(
            session_id,
            expected_goal_id=created.id,
            continue_from_latest_completion=True,
        )
        == 0
    )
    assert len(turns.started) == 1
    assert (
        goals.reconcile_turn_settlements(
            session_id,
            expected_goal_id=created.id,
            continue_from_latest_completion=True,
        )
        == 0
    )
    assert len(turns.started) == 1


def test_replayed_older_completion_cannot_restart_after_newer_interruption(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    completed, completed_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    interrupted, interrupted_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.INTERRUPTED,
    )
    interrupted = Turn(
        id=interrupted.id,
        thread_id=interrupted.thread_id,
        ordinal=2,
        prompt=interrupted.prompt,
        goal_id=interrupted.goal_id,
        status=interrupted.status,
        started_at=interrupted.started_at,
        completed_at=interrupted.completed_at,
    )
    turns.snapshots[completed.id] = _Snapshot(completed, (completed_item,))
    turns.snapshots[interrupted.id] = _Snapshot(
        interrupted,
        (interrupted_item,),
    )

    goals.on_turn_settled(interrupted)
    goals.on_turn_settled(completed)
    goals.on_turn_settled(completed)

    current = goals.read(session_id)
    assert current is not None
    assert current.status is ThreadGoalStatus.ACTIVE
    assert current.tokens_used == 84
    assert not turns.started


def test_continuation_waits_until_all_terminal_goal_turns_are_accounted(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    first, first_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    second, second_item = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    second = Turn(
        id=second.id,
        thread_id=second.thread_id,
        ordinal=2,
        prompt=second.prompt,
        goal_id=second.goal_id,
        status=second.status,
        started_at=second.started_at,
        completed_at=second.completed_at,
    )
    turns.snapshots[first.id] = _Snapshot(first, (first_item,))
    turns.snapshots[second.id] = _Snapshot(second, (second_item,))

    goals.on_turn_settled(second)
    assert not turns.started
    with pytest.raises(ConflictError, match="settlement is incomplete"):
        goals.continue_goal(
            session_id,
            expected_goal_id=created.id,
        )

    goals.on_turn_settled(first)
    continued = goals.continue_goal(
        session_id,
        expected_goal_id=created.id,
    )

    current = goals.read(session_id)
    assert current is not None
    assert current.tokens_used == 84
    assert continued.disposition is GoalContinueDisposition.STARTED
    assert len(turns.started) == 1


def test_recovery_replays_a_terminal_turn_missing_from_the_goal_ledger(
    tmp_path,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    failed, completion = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.FAILED,
        error_message="provider disconnected",
    )
    turns.snapshots[failed.id] = _Snapshot(failed, (completion,))

    assert goals.recover_incomplete() == 1
    assert goals.recover_incomplete() == 0

    current = goals.read(session_id)
    assert current is not None
    assert current.status is ThreadGoalStatus.BLOCKED
    assert current.tokens_used == 42
    assert current.time_used_seconds == 3
    assert not turns.started


def test_recovery_continues_after_ledger_commit_before_turn_submission(
    tmp_path,
    monkeypatch,
) -> None:
    goals, turns, session_id = _extension(tmp_path)
    created = goals.create(session_id, objective="Ship", start=False)
    completed, completion = _terminal_turn(
        thread_id=session_id,
        goal_id=created.id,
        status=TurnStatus.COMPLETED,
    )
    turns.snapshots[completed.id] = _Snapshot(completed, (completion,))
    monkeypatch.setattr(goals, "_continue_if_idle_locked", lambda _goal: None)

    goals.on_turn_settled(completed)
    assert not turns.started

    restarted = GoalExtension(
        ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False)),
        turns,
    )
    assert restarted.recover_incomplete() == 0
    assert len(turns.started) == 1


def test_stale_goal_tool_cannot_update_a_replacement_goal(tmp_path) -> None:
    goals, turns, session_id = _extension(tmp_path)
    original = goals.create(session_id, objective="First Goal", start=False)
    active = Turn(
        thread_id=session_id,
        ordinal=1,
        prompt="work",
        goal_id=original.id,
        status=TurnStatus.RUNNING,
        started_at=utc_now(),
    )
    turns.active = active
    turns.snapshots[active.id] = _Snapshot(active)
    goals.clear(session_id, expected_goal_id=original.id)
    replacement = goals.create(
        session_id,
        objective="Replacement Goal",
        start=False,
    )

    with pytest.raises(ConflictError):
        goals.update_goal(
            GoalRuntimeContext(
                thread_id=session_id,
                goal_id=original.id,
                turn_id=active.id,
            ),
            status="complete",
            reason="stale request",
        )

    current = goals.read(session_id)
    assert current is not None
    assert current.id == replacement.id
    assert current.status is ThreadGoalStatus.ACTIVE
