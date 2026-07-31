from __future__ import annotations

import asyncio
import time
from pathlib import Path

from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.turn import TurnStatus
from core.events import Event, TurnStarted


class _HangingSession:
    def load_history(self, _messages) -> None:
        return None

    async def run_stream(self, _operation):
        yield Event("started", TurnStarted())
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        return None


class _HangingFactory:
    def create(self, *, workspace, model, approval_callback):
        return _HangingSession()


def _wait_for_status(
    application: DeepCodeApplication,
    turn_id: str,
    expected: TurnStatus,
) -> None:
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if application.turns.read(turn_id).turn.status is expected:
            return
        time.sleep(0.01)
    actual = application.turns.read(turn_id).turn.status
    raise AssertionError(f"Turn stayed {actual.value}; expected {expected.value}")


def _trusted_thread(
    application: DeepCodeApplication,
    workspace: Path,
    *,
    title: str,
):
    workspace.mkdir()
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    return application.threads.start(project.id, title=title)


def test_canonical_workspaces_from_different_projects_can_run_concurrently(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_HangingFactory(),
        max_concurrent_turns=2,
        run_automation_scheduler=False,
    )
    first_thread = _trusted_thread(
        application,
        tmp_path / "first-project",
        title="first",
    )
    second_thread = _trusted_thread(
        application,
        tmp_path / "second-project",
        title="second",
    )
    first = application.turns.start(first_thread.id, prompt="first")
    second = application.turns.start(second_thread.id, prompt="second")

    try:
        _wait_for_status(application, first.turn.id, TurnStatus.RUNNING)
        _wait_for_status(application, second.turn.id, TurnStatus.RUNNING)
        assert {
            claim.turn_id for claim in application.execution_coordinator.active_claims
        } == {first.turn.id, second.turn.id}
    finally:
        application.turns.interrupt(first_thread.id, first.turn.id)
        application.turns.interrupt(second_thread.id, second.turn.id)
        application.close()


def test_threads_sharing_a_canonical_workspace_are_serialized(
    tmp_path: Path,
) -> None:
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=_HangingFactory(),
        max_concurrent_turns=2,
        run_automation_scheduler=False,
    )
    workspace = tmp_path / "shared-project"
    workspace.mkdir()
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    first_thread = application.threads.start(project.id, title="first")
    second_thread = application.threads.start(project.id, title="second")
    first = application.turns.start(first_thread.id, prompt="first")

    try:
        _wait_for_status(application, first.turn.id, TurnStatus.RUNNING)
        second = application.turns.start(second_thread.id, prompt="second")
        _wait_for_status(application, second.turn.id, TurnStatus.QUEUED)

        time.sleep(0.15)
        assert application.turns.read(second.turn.id).turn.status is TurnStatus.QUEUED
        assert {
            claim.turn_id for claim in application.execution_coordinator.active_claims
        } == {first.turn.id}

        application.turns.interrupt(first_thread.id, first.turn.id)
        _wait_for_status(application, first.turn.id, TurnStatus.INTERRUPTED)
        _wait_for_status(application, second.turn.id, TurnStatus.RUNNING)
        application.turns.interrupt(second_thread.id, second.turn.id)
        _wait_for_status(application, second.turn.id, TurnStatus.INTERRUPTED)
    finally:
        application.close()
