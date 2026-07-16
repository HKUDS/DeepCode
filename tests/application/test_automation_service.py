from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ProjectNotTrustedError
from core.domain import (
    AutomationRunStatus,
    AutomationScheduleKind,
    AutomationStatus,
    TrustState,
    TurnStatus,
)
from core.domain.common import utc_now
from core.events import AgentMessage, Event, TaskComplete, TurnStarted


class AutomationSession:
    def __init__(self, *, hang: bool) -> None:
        self.hang = hang
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("1", TurnStarted())
        if self.hang:
            await asyncio.Event().wait()
        yield Event("2", AgentMessage("automation complete"))
        yield Event("3", TaskComplete("automation complete", "completed"))
        self.history.append({"role": "assistant", "content": "automation complete"})

    async def aclose(self) -> None:
        return None


class AutomationFactory:
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.sessions: list[AutomationSession] = []

    def create(self, *, workspace, model, approval_callback):
        session = AutomationSession(hang=self.hang)
        self.sessions.append(session)
        return session


def _wait_for_run(
    application: DeepCodeApplication,
    automation_id: str,
    status: AutomationRunStatus,
):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        runs = application.automations.list_runs(automation_id)
        if runs and runs[0].status is status:
            return runs[0]
        time.sleep(0.01)
    raise AssertionError(
        f"automation did not reach {status}: "
        f"{application.automations.list_runs(automation_id)}"
    )


def test_automation_creation_requires_trust_and_owns_a_canonical_goal_thread(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    try:
        with pytest.raises(ProjectNotTrustedError):
            application.automations.create(
                project_id=project.id,
                name="Repository review",
                prompt="Review the repository",
                schedule_kind=AutomationScheduleKind.MANUAL,
            )

        application.projects.update(project.id, trust_state=TrustState.TRUSTED)
        created = application.automations.create(
            project_id=project.id,
            name="Repository review",
            prompt="Review the repository",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        assert created.automation.thread_id == created.thread.id
        assert created.thread.mode.value == "goal"
        assert created.automation.next_run_at is None
        session = application.session_store.get_session(created.thread.id)
        assert session is not None
        assert session.metadata["mode"] == "goal"
    finally:
        application.close()


def test_manual_automation_runs_through_normal_turn_and_session_lifecycle(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = AutomationFactory()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Fix regressions",
            prompt="Find and fix regressions, then verify the result",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
        execution = application.automations.run_now(created.automation.id)
        assert execution.turn is not None
        run = _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.COMPLETED,
        )
        assert run.turn_id == execution.turn.id
        assert (
            application.turns.read(execution.turn.id).turn.status
            is TurnStatus.COMPLETED
        )
        session = application.session_store.get_session(created.thread.id)
        assert session is not None
        assert [message.role for message in session.messages] == [
            "user",
            "assistant",
        ]
        assert session.messages[0].content == created.automation.prompt
    finally:
        application.close()


def test_interval_automation_coalesces_missed_runs_and_skips_when_busy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = AutomationFactory(hang=True)
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Continuous review",
            prompt="Review the current repository state",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=60,
        )
        first_due = created.automation.next_run_at
        assert first_due is not None
        first_runs = application.automations.run_due(first_due)
        assert len(first_runs) == 1
        running = _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.RUNNING,
        )
        assert running.turn_id is not None

        second_runs = application.automations.run_due(first_due + timedelta(seconds=61))
        assert len(second_runs) == 1
        assert second_runs[0].status is AutomationRunStatus.SKIPPED
        assert "still active" in second_runs[0].detail
        refreshed = application.automations.read(created.automation.id)
        assert refreshed.next_run_at is not None
        assert refreshed.next_run_at > first_due + timedelta(seconds=61)

        application.turns.interrupt(running.turn_id)
        _wait_for_run(
            application,
            created.automation.id,
            AutomationRunStatus.SKIPPED,
        )
    finally:
        application.close()


def test_pause_and_resume_recalculate_the_next_occurrence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        created = application.automations.create(
            project_id=project.id,
            name="Scheduled maintenance",
            prompt="Perform repository maintenance",
            schedule_kind=AutomationScheduleKind.INTERVAL,
            interval_seconds=3600,
        )
        paused = application.automations.update(
            created.automation.id,
            status=AutomationStatus.PAUSED,
        )
        assert paused.next_run_at is None
        resumed_at = utc_now()
        resumed = application.automations.update(
            created.automation.id,
            status=AutomationStatus.ENABLED,
        )
        assert resumed.next_run_at is not None
        assert resumed.next_run_at >= resumed_at + timedelta(seconds=3599)
    finally:
        application.close()
