from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from core.application import DeepCodeApplication
from core.application.goal_evaluator import SemanticDecision
from core.domain import (
    GoalAttemptStatus,
    GoalBudget,
    GoalStatus,
    GoalVerdict,
    TrustState,
    Turn,
)
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.persistence.execution_repository import TurnRepository
from core.sessions import SessionStore


class CompletingSession:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.last_usage = {"total_tokens": 7}

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("1", TurnStarted())
        answer = f"worked on: {op.text.splitlines()[0]}"
        yield Event("2", AgentMessage(answer))
        yield Event("3", TaskComplete(answer, "completed"))
        self.history.append({"role": "assistant", "content": answer})

    async def aclose(self) -> None:
        return None


class CompletingFactory:
    def create(self, **_kwargs):
        return CompletingSession()


class SequencedSemanticEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, _context):
        self.calls += 1
        verdict = GoalVerdict.CONTINUE if self.calls == 1 else GoalVerdict.COMPLETE
        return SemanticDecision(
            verdict=verdict,
            reason=(
                "One more implementation pass is required."
                if verdict is GoalVerdict.CONTINUE
                else "The Goal and acceptance criteria are satisfied."
            ),
            evidence_refs=(),
            provider_name="test",
            model_id="test-evaluator",
            tokens_used=5,
        )


def _write_config(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "connection": "router",
                        "model": "moonshotai/kimi-k2.5",
                    }
                },
                "providers": {
                    "profiles": {
                        "router": {
                            "label": "Router",
                            "template": "openrouter",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_goal_coordinator_creates_durable_ordinary_turns_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_config(home)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    semantic = SequencedSemanticEvaluator()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        max_concurrent_turns=2,
        session_factory=CompletingFactory(),
        session_store=sessions,
        semantic_goal_evaluator=semantic,
    )
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(
            project.id,
            title="Durable Goal",
            connection_id="router",
            model="moonshotai/kimi-k2.5",
        )
        created = application.goals.create(
            thread.id,
            objective="Implement and verify the feature",
            acceptance_criteria=("The feature works",),
            budget=GoalBudget(max_attempts=3, max_elapsed_seconds=60),
        )

        application.goal_coordinator.start(thread.id)

        deadline = time.monotonic() + 5
        record = created
        while time.monotonic() < deadline:
            record = application.goals.read(thread.id) or record
            if record.goal.status is GoalStatus.COMPLETED:
                break
            time.sleep(0.01)

        assert record.goal.status is GoalStatus.COMPLETED
        assert record.attempt_count == 2
        assert len(record.evaluations) == 2
        assert semantic.calls == 2
        assert record.goal.tokens_used == 24
        with application.database.read() as connection:
            turns = TurnRepository(connection).list_for_thread(thread.id)
        assert len(turns) == 2
        assert all(turn.status is TurnStatus.COMPLETED for turn in turns)
        assert [turn.goal_attempt_id for turn in turns] == [
            attempt.id for attempt in record.attempts
        ]
        canonical = sessions.get_session(thread.id)
        assert canonical is not None
        assert [message.role for message in canonical.messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
    finally:
        application.close()


def test_restart_pauses_goal_and_does_not_replay_queued_goal_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    _write_config(home)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    database_path = tmp_path / "state.sqlite3"
    application = DeepCodeApplication.open(
        database_path,
        session_factory=CompletingFactory(),
        session_store=sessions,
        semantic_goal_evaluator=SequencedSemanticEvaluator(),
    )
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(project.id, title="Crash recovery")
        created = application.goals.create(
            thread.id,
            objective="Perform a mutating task exactly once",
        )
        attempt = application.goals.begin_attempt(
            thread.id,
            expected_revision=created.goal.revision,
        )
        queued = Turn(
            thread_id=thread.id,
            ordinal=1,
            prompt="Continue the durable Goal",
            goal_id=created.goal.id,
            goal_definition_revision=created.goal.definition_revision,
            goal_attempt_id=attempt.id,
        )
        with application.database.transaction() as connection:
            TurnRepository(connection).add(queued)
        application.goals.update_attempt(
            thread.id,
            expected_revision=created.goal.revision,
            attempt_id=attempt.id,
            status=GoalAttemptStatus.QUEUED,
            turn_id=queued.id,
        )
    finally:
        application.close()

    recovered = DeepCodeApplication.open(
        database_path,
        session_factory=CompletingFactory(),
        session_store=sessions,
        semantic_goal_evaluator=SequencedSemanticEvaluator(),
    )
    try:
        record = recovered.goals.read(thread.id)
        assert record is not None
        assert record.goal.status is GoalStatus.PAUSED
        assert "restarted" in (record.goal.last_reason or "")
        assert record.latest_attempt is not None
        assert record.latest_attempt.status is GoalAttemptStatus.INTERRUPTED
        assert recovered.turns.read(queued.id).turn.status is TurnStatus.INTERRUPTED
        assert not recovered.executions.is_active(queued.id)
    finally:
        recovered.close()
