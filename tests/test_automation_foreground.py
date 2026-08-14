from __future__ import annotations

import asyncio
import builtins
import json
from pathlib import Path

import pytest

from cli import automation_cli
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.approval import ApprovalStatus
from core.domain.automation import AutomationRunStatus, AutomationScheduleKind
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.sessions import SessionStore


class _ApprovalGoalSession:
    def __init__(self, factory: _ApprovalGoalFactory, **kwargs) -> None:
        self._factory = factory
        self._approval_callback = kwargs["approval_callback"]
        self._goal_runtime = kwargs["goal_runtime"]
        self.history: list[dict[str, str]] = []

    def load_history(self, messages) -> None:
        self.history = list(messages)

    async def run_stream(self, operation):
        self.history.append({"role": "user", "content": operation.text})
        yield Event("start", TurnStarted())
        approved = await self._approval_callback(
            "write_file",
            {"path": "result.txt", "content": "verified"},
            "write the verified result",
        )
        self._factory.decisions.append(approved)
        status = "complete" if approved else "blocked"
        reason = "verified result written" if approved else "approval denied"
        self._goal_runtime.request(status=status, reason=reason)
        yield Event("message", AgentMessage(reason))
        yield Event("complete", TaskComplete(reason, status))
        self.history.append({"role": "assistant", "content": reason})

    async def aclose(self) -> None:
        await asyncio.sleep(0)


class _ApprovalGoalFactory:
    def __init__(self) -> None:
        self.decisions: list[bool] = []

    def create(self, **kwargs):
        return _ApprovalGoalSession(self, **kwargs)


def _seed(
    tmp_path: Path,
    factory: _ApprovalGoalFactory,
) -> tuple[DeepCodeApplication, str, Path, SessionStore]:
    database_path = tmp_path / "state.sqlite3"
    session_store = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        database_path,
        session_store=session_store,
        session_factory=factory,
        host_surface="automation_foreground_test",
        run_automation_scheduler=False,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    created = application.automations.create(
        project_id=project.id,
        name="Approval verification",
        prompt="Produce and verify a result",
        schedule_kind=AutomationScheduleKind.MANUAL,
    )
    return application, created.automation.id, database_path, session_store


def _audit(
    database_path: Path,
    session_store: SessionStore,
    factory: _ApprovalGoalFactory,
    automation_id: str,
):
    application = DeepCodeApplication.open(
        database_path,
        session_store=session_store,
        session_factory=factory,
        host_surface="automation_foreground_audit",
        run_automation_scheduler=False,
    )
    run = application.automations.list_runs(automation_id).runs[0]
    snapshot = application.turns.read(run.turn_id) if run.turn_id is not None else None
    return application, run, snapshot


@pytest.mark.parametrize(
    ("answer", "expected_status"),
    [
        ("y", ApprovalStatus.APPROVED_ONCE),
        ("a", ApprovalStatus.APPROVED_SESSION),
    ],
)
def test_foreground_run_waits_for_interactive_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    expected_status: ApprovalStatus,
) -> None:
    factory = _ApprovalGoalFactory()
    application, automation_id, database_path, session_store = _seed(
        tmp_path,
        factory,
    )
    prompts: list[str] = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda prompt: prompts.append(prompt) or answer,
    )

    assert (
        automation_cli.run(
            ["run", automation_id],
            application_factory=lambda: application,
        )
        == 0
    )

    assert "completed" in capsys.readouterr().out
    assert prompts
    assert "write_file" in prompts[0]
    assert "write the verified result" in prompts[0]
    assert factory.decisions == [True]
    audit, run, snapshot = _audit(
        database_path,
        session_store,
        factory,
        automation_id,
    )
    try:
        assert run.status is AutomationRunStatus.COMPLETED
        assert snapshot is not None
        assert snapshot.approvals[0].status is expected_status
    finally:
        audit.close()


def test_foreground_run_denial_settles_as_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ApprovalGoalFactory()
    application, automation_id, database_path, session_store = _seed(
        tmp_path,
        factory,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")

    assert (
        automation_cli.run(
            ["run", automation_id],
            application_factory=lambda: application,
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "blocked" in output
    assert "approval denied" in output
    assert factory.decisions == [False]
    audit, run, snapshot = _audit(
        database_path,
        session_store,
        factory,
        automation_id,
    )
    try:
        assert run.status is AutomationRunStatus.BLOCKED
        assert snapshot is not None
        assert snapshot.approvals[0].status is ApprovalStatus.DENIED
    finally:
        audit.close()


def test_foreground_returns_an_initially_blocked_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ApprovalGoalFactory()
    application, automation_id, _database_path, _session_store = _seed(
        tmp_path,
        factory,
    )

    def reject_submission(*_args, **_kwargs):
        raise RuntimeError("injected submission conflict")

    monkeypatch.setattr(
        application.turns,
        "start_with_participant",
        reject_submission,
    )

    assert (
        automation_cli.run(
            ["run", automation_id, "--json"],
            application_factory=lambda: application,
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "blocked"
    assert "injected submission conflict" in payload["run"]["detail"]
    assert payload["turn"] is None


def test_noninteractive_approval_fails_fast_and_settles_local_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = _ApprovalGoalFactory()
    application, automation_id, database_path, session_store = _seed(
        tmp_path,
        factory,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def unexpected_input(_prompt: str) -> str:
        raise AssertionError("non-interactive Automation must not prompt")

    monkeypatch.setattr(builtins, "input", unexpected_input)

    assert (
        automation_cli.run(
            ["run", automation_id, "--json"],
            application_factory=lambda: application,
        )
        == 1
    )

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "APPROVAL_REQUIRED"
    assert error["details"]["toolName"] == "write_file"
    assert error["details"]["reason"] == "write the verified result"
    assert error["details"]["cleanup"] == "interrupted_local_owner"
    assert factory.decisions == []

    audit, run, snapshot = _audit(
        database_path,
        session_store,
        factory,
        automation_id,
    )
    try:
        assert run.status is AutomationRunStatus.BLOCKED
        assert snapshot is not None
        assert snapshot.turn.status is TurnStatus.INTERRUPTED
        assert snapshot.approvals[0].status is ApprovalStatus.CANCELLED
        assert audit.turns.active_for_thread(run.thread_id) is None
    finally:
        audit.close()
