from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.domain.thread_goal import (
    GoalDecisionSource,
    ThreadGoal,
    ThreadGoalStatus,
)
from core.sessions import SessionStore
from core.sessions.thread_goal_store import (
    THREAD_GOAL_SCHEMA_VERSION,
    ThreadGoalConflictError,
    ThreadGoalIdentityRetiredError,
    ThreadGoalLedgerCorruptError,
    ThreadGoalSessionNotFoundError,
    ThreadGoalStore,
)


def _store(tmp_path) -> tuple[SessionStore, ThreadGoalStore, str]:
    sessions = SessionStore(tmp_path / "sessions", use_index=False)
    session = sessions.create_session(title="Thread Goal test")
    return sessions, ThreadGoalStore(sessions), session.session_id


def _ledger_entries(sessions: SessionStore, session_id: str) -> list[dict]:
    ledger = sessions.root / session_id / "goal.jsonl"
    return [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_v2_store_writes_one_complete_transition_per_mutation(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = ThreadGoal(
        thread_id=session_id,
        objective="Ship",
        token_budget=500,
    )
    goals.create(goal)
    edited = goal.edit(
        "Ship without changing the public API",
        token_budget=700,
        skill_ids=(),
    )
    goals.update(
        session_id,
        expected_goal_id=goal.id,
        transform=lambda _current: edited,
        reason="objective edited",
        source="user",
    )

    entries = _ledger_entries(sessions, session_id)

    assert len(entries) == 2
    assert {entry["schemaVersion"] for entry in entries} == {THREAD_GOAL_SCHEMA_VERSION}
    assert all(entry["_type"] == "thread_goal.snapshot" for entry in entries)
    assert goals.read(session_id) == edited


def test_outcome_preserves_the_decision_across_usage_snapshots(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    created = goals.create(ThreadGoal(thread_id=session_id, objective="Ship"))
    completed = goals.update(
        session_id,
        expected_goal_id=created.id,
        transform=lambda current: current.agent_transition(ThreadGoalStatus.COMPLETE),
        reason="Tests passed and the requested change is present.",
        source="agent",
        turn_id="turn_deciding",
    )
    assert not goals.has_turn_settlement(
        session_id,
        goal_id=created.id,
        turn_id="turn_deciding",
    )
    goals.update(
        session_id,
        expected_goal_id=created.id,
        transform=lambda current: current.add_usage(
            tokens=42,
            elapsed_seconds=3,
        ),
        reason="turn completed",
        source="runtime",
        turn_id="turn_deciding",
    )
    assert goals.has_turn_settlement(
        session_id,
        goal_id=created.id,
        turn_id="turn_deciding",
    )

    # Reopen both stores so the assertion proves recovery from disk rather than
    # an in-memory projection left behind by the deciding process.
    reopened = ThreadGoalStore(SessionStore(sessions.root))
    record = reopened.read_record(session_id)

    assert reopened.has_turn_settlement(
        session_id,
        goal_id=created.id,
        turn_id="turn_deciding",
    )
    assert record.goal is not None
    assert record.goal.tokens_used == 42
    assert record.outcome is not None
    assert record.outcome.status is ThreadGoalStatus.COMPLETE
    assert record.outcome.reason == (
        "Tests passed and the requested change is present."
    )
    assert record.outcome.source is GoalDecisionSource.AGENT
    assert record.outcome.decided_by_turn_id == "turn_deciding"
    assert record.outcome.decided_at == completed.updated_at


def test_resuming_a_terminal_goal_clears_its_previous_outcome(tmp_path) -> None:
    _sessions, goals, session_id = _store(tmp_path)
    created = goals.create(ThreadGoal(thread_id=session_id, objective="Ship"))
    goals.update(
        session_id,
        expected_goal_id=created.id,
        transform=lambda current: current.agent_transition(ThreadGoalStatus.BLOCKED),
        reason="Dependency is unavailable.",
        source="agent",
        turn_id="turn_blocked",
    )
    goals.update(
        session_id,
        expected_goal_id=created.id,
        transform=lambda current: current.user_transition(ThreadGoalStatus.ACTIVE),
        reason="resumed by user",
        source="user",
    )

    record = goals.read_record(session_id)

    assert record.goal is not None
    assert record.goal.status is ThreadGoalStatus.ACTIVE
    assert record.outcome is None


def test_goal_identity_is_the_cross_process_cas_token(tmp_path) -> None:
    sessions, first_store, session_id = _store(tmp_path)
    second_store = ThreadGoalStore(
        SessionStore(sessions.root, use_index=False),
    )
    first = ThreadGoal(thread_id=session_id, objective="First")
    first_store.create(first)
    assert first_store.clear(session_id, expected_goal_id=first.id)
    second = ThreadGoal(thread_id=session_id, objective="Second")
    first_store.create(second)

    with pytest.raises(ThreadGoalConflictError, match="different identity"):
        second_store.update(
            session_id,
            expected_goal_id=first.id,
            transform=lambda current: current.edit(
                "Stale edit",
                token_budget=None,
                skill_ids=(),
            ),
            reason="stale edit",
            source="user",
        )

    assert second_store.read(session_id) == second


def test_cleared_goal_identity_cannot_be_reused_by_create_or_provision(
    tmp_path,
) -> None:
    _sessions, goals, session_id = _store(tmp_path)
    retired = ThreadGoal(thread_id=session_id, objective="First")
    goals.create(retired)
    assert goals.clear(session_id, expected_goal_id=retired.id)

    with pytest.raises(ThreadGoalIdentityRetiredError, match="cannot be reused"):
        goals.provision(retired)
    with pytest.raises(ThreadGoalIdentityRetiredError, match="cannot be reused"):
        goals.create(retired)

    replacement = ThreadGoal(thread_id=session_id, objective="Replacement")
    created, was_created = goals.provision(replacement)
    assert was_created
    assert created == replacement
    assert goals.read(session_id) == replacement


def test_replacing_completed_goal_checks_new_identity_before_clearing(
    tmp_path,
) -> None:
    _sessions, goals, session_id = _store(tmp_path)
    retired = ThreadGoal(thread_id=session_id, objective="Retired target")
    goals.create(retired)
    assert goals.clear(session_id, expected_goal_id=retired.id)
    current = ThreadGoal(thread_id=session_id, objective="Keep this Goal")
    goals.create(current)
    completed = goals.update(
        session_id,
        expected_goal_id=current.id,
        transform=lambda goal: goal.agent_transition(ThreadGoalStatus.COMPLETE),
        reason="Current Goal completed",
        source="agent",
    )

    with pytest.raises(ThreadGoalIdentityRetiredError, match="cannot be reused"):
        goals.provision_replacing_completed(
            retired,
            expected_current_goal_id=completed.id,
        )

    assert goals.read(session_id) == completed


def test_v1_ledger_is_read_losslessly_without_rewriting(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    fixture = (
        Path(__file__).parents[1] / "fixtures" / "goal_ledger_v1_legacy.jsonl"
    ).read_text(encoding="utf-8")
    ledger = sessions.root / session_id / "goal.jsonl"
    original = fixture.replace("__SESSION_ID__", session_id)
    ledger.write_text(original, encoding="utf-8")

    goal = goals.read(session_id)

    assert goal is not None
    assert goal.id == "goal_legacyfixture"
    assert goal.objective == (
        "Preserve the legacy behavior\n\nCompletion conditions:\n"
        "- Existing tests still pass\n"
        "- The old command remains available"
    )
    assert goal.status is ThreadGoalStatus.PAUSED
    assert goal.token_budget == 12_000
    assert goal.tokens_used == 17
    assert goal.time_used_seconds == 9
    assert ledger.read_text(encoding="utf-8") == original


def test_first_mutation_after_v1_cutover_appends_only_v2(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    fixture = (
        Path(__file__).parents[1] / "fixtures" / "goal_ledger_v1_legacy.jsonl"
    ).read_text(encoding="utf-8")
    ledger = sessions.root / session_id / "goal.jsonl"
    ledger.write_text(
        fixture.replace("__SESSION_ID__", session_id),
        encoding="utf-8",
    )
    legacy = goals.read(session_id)
    assert legacy is not None
    edited = legacy.edit(
        "Preserve every public compatibility boundary",
        token_budget=legacy.token_budget,
        skill_ids=legacy.skill_ids,
    )

    goals.update(
        session_id,
        expected_goal_id=legacy.id,
        transform=lambda _current: edited,
        reason="objective edited",
        source="user",
    )

    entries = _ledger_entries(sessions, session_id)
    assert [entry["schemaVersion"] for entry in entries] == [1, 2]
    assert goals.read(session_id) == edited


def test_v1_auxiliary_records_are_read_only_and_do_not_enter_v2_runtime(
    tmp_path,
) -> None:
    sessions, goals, session_id = _store(tmp_path)
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "goal_ledger_v1_with_auxiliary_records.jsonl"
    ).read_text(encoding="utf-8")
    ledger = sessions.root / session_id / "goal.jsonl"
    original = fixture.replace("__SESSION_ID__", session_id)
    ledger.write_text(original, encoding="utf-8")

    goal = goals.read(session_id)

    assert goal is not None
    assert goal.id == "goal_legacyauxiliary"
    assert goal.status is ThreadGoalStatus.COMPLETE
    assert goal.tokens_used == 12
    assert goal.time_used_seconds == 4
    assert ledger.read_text(encoding="utf-8") == original


def test_legacy_writer_cannot_append_after_v2_cutover(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = ThreadGoal(thread_id=session_id, objective="Ship")
    goals.create(goal)
    legacy = (
        Path(__file__).parents[1] / "fixtures" / "goal_ledger_v1_legacy.jsonl"
    ).read_text(encoding="utf-8")
    ledger = sessions.root / session_id / "goal.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(legacy.replace("__SESSION_ID__", session_id))

    with pytest.raises(ThreadGoalLedgerCorruptError, match="cannot follow"):
        goals.read(session_id)


def test_store_requires_a_canonical_session_and_fails_closed(tmp_path) -> None:
    goals = ThreadGoalStore(SessionStore(tmp_path / "sessions", use_index=False))
    with pytest.raises(ThreadGoalSessionNotFoundError):
        goals.create(ThreadGoal(thread_id="missing", objective="No session"))

    sessions, goals, session_id = _store(tmp_path / "corrupt")
    (sessions.root / session_id / "goal.jsonl").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )
    with pytest.raises(ThreadGoalLedgerCorruptError):
        goals.read(session_id)


def test_uncommitted_tail_is_ignored_then_repaired_before_next_write(
    tmp_path,
) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = ThreadGoal(thread_id=session_id, objective="Survive a torn write")
    goals.create(goal)
    ledger = sessions.root / session_id / "goal.jsonl"
    committed = ledger.read_bytes()
    with ledger.open("ab") as handle:
        handle.write(b'{"_type":"thread_goal.snapshot","schemaVersion":2')

    assert goals.read(session_id) == goal

    edited = goal.edit(
        "Continue after recovering the committed state",
        token_budget=None,
        skill_ids=(),
    )
    goals.update(
        session_id,
        expected_goal_id=goal.id,
        transform=lambda _current: edited,
        reason="recover after torn write",
        source="runtime",
    )

    assert ledger.read_bytes().startswith(committed)
    assert ledger.read_bytes().endswith(b"\n")
    assert len(_ledger_entries(sessions, session_id)) == 2
    assert goals.read(session_id) == edited


def test_invalid_committed_record_still_fails_closed(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goals.create(ThreadGoal(thread_id=session_id, objective="Fail closed"))
    ledger = sessions.root / session_id / "goal.jsonl"
    with ledger.open("ab") as handle:
        handle.write(b"{not-json}\n")

    with pytest.raises(ThreadGoalLedgerCorruptError, match="line 2"):
        goals.read(session_id)


def test_failed_append_does_not_publish_a_transition(tmp_path, monkeypatch) -> None:
    _sessions, goals, session_id = _store(tmp_path)
    goal = ThreadGoal(thread_id=session_id, objective="Keep committed state")
    goals.create(goal)

    def fail_before_write(_directory, _entry) -> None:
        raise OSError("injected append failure")

    monkeypatch.setattr(goals, "_append_entry", fail_before_write)
    with pytest.raises(OSError, match="injected"):
        goals.update(
            session_id,
            expected_goal_id=goal.id,
            transform=lambda current: current.edit(
                "This transition must not become visible",
                token_budget=None,
                skill_ids=(),
            ),
            reason="fault injection",
            source="runtime",
        )

    assert goals.read(session_id) == goal


def test_concurrent_usage_updates_do_not_lose_committed_state(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = ThreadGoal(thread_id=session_id, objective="Serialize usage")
    goals.create(goal)
    stores = [
        ThreadGoalStore(SessionStore(sessions.root, use_index=False)) for _ in range(4)
    ]

    def add_usage(index: int) -> None:
        store = stores[index % len(stores)]
        store.update(
            session_id,
            expected_goal_id=goal.id,
            transform=lambda current: current.add_usage(
                tokens=1,
                elapsed_seconds=1,
            ),
            reason="record turn usage",
            source="runtime",
        )

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        list(executor.map(add_usage, range(40)))

    current = goals.read(session_id)
    assert current is not None
    assert current.tokens_used == 40
    assert current.time_used_seconds == 40
    assert len(_ledger_entries(sessions, session_id)) == 41


def test_concurrent_turn_settlement_is_applied_exactly_once(tmp_path) -> None:
    sessions, goals, session_id = _store(tmp_path)
    goal = goals.create(ThreadGoal(thread_id=session_id, objective="Settle once"))
    stores = [
        ThreadGoalStore(SessionStore(sessions.root, use_index=False)) for _ in range(4)
    ]

    def settle(store: ThreadGoalStore) -> bool:
        _updated, applied = store.settle_turn(
            session_id,
            expected_goal_id=goal.id,
            turn_id="turn_shared_settlement",
            transform=lambda current: current.add_usage(
                tokens=7,
                elapsed_seconds=2,
            ),
            reason="turn completed",
        )
        return applied

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        applied = list(executor.map(settle, stores))

    current = goals.read(session_id)
    assert current is not None
    assert applied.count(True) == 1
    assert current.tokens_used == 7
    assert current.time_used_seconds == 2
    assert len(_ledger_entries(sessions, session_id)) == 2
