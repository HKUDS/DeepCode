"""Offline tests for the headless adapter over the ordinary Turn runtime.

The host does not pretend it can verify arbitrary coding work.  A requested
verification command is part of the model-visible objective; the associated
Agent inspects that evidence and requests either ``complete`` or ``blocked``.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.loop_cli as loop_cli  # noqa: E402
import core.agent_setup as agent_setup  # noqa: E402
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402


class _Provider:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        self.requests.append(kwargs)
        self.calls += 1
        if self.calls % 2:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id=f"goal-complete-{self.calls}",
                        name="update_goal",
                        arguments={
                            "status": self.status,
                            "reason": (
                                "The requested evidence is sufficient."
                                if self.status == "complete"
                                else "The requested evidence is still failing."
                            ),
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="done", finish_reason="stop")


class _Profile:
    model = "fake-model"


class _BlockingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__("complete")
        self.entered = threading.Event()
        self.release = threading.Event()

    async def chat_with_retry(self, **kwargs: Any):
        if not self.entered.is_set():
            self.entered.set()
            await asyncio.to_thread(self.release.wait)
        return await super().chat_with_retry(**kwargs)


class _BlockingFinalResponseProvider(_Provider):
    def __init__(self) -> None:
        super().__init__("complete")
        self.final_started = threading.Event()
        self.release_final = threading.Event()

    async def chat_with_retry(self, **kwargs: Any):
        if self.calls == 1:
            self.final_started.set()
            await asyncio.to_thread(self.release_final.wait)
        return await super().chat_with_retry(**kwargs)


def _configure(
    monkeypatch,
    tmp_path,
    provider: _Provider,
) -> list[dict[str, Any]]:
    provider_options: list[dict[str, Any]] = []

    def get_provider(**kwargs):
        provider_options.append(kwargs)
        return provider, _Profile()

    monkeypatch.setattr(agent_setup, "get_workflow_provider", get_provider)
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("core.sessions.store._DEFAULT_STORE", None)
    return provider_options


def _create_idle_goal(
    workspace: Path,
    *,
    objective: str = "Finish the existing Goal",
    token_budget: int | None = None,
    tokens_used: int = 0,
) -> tuple[str, str]:
    from core.application import DeepCodeApplication
    from core.domain import TrustState

    application = DeepCodeApplication.open()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        thread = application.threads.start(
            project.id,
            title=objective,
            session_kind="headless",
        )
        goal = application.goals.create(
            thread.id,
            objective=objective,
            token_budget=token_budget,
            start=False,
        )
        if tokens_used:
            goal = application.thread_goal_store.update(
                thread.id,
                expected_goal_id=goal.id,
                transform=lambda current: current.add_usage(
                    tokens=tokens_used,
                    elapsed_seconds=1,
                ),
                reason="seed usage",
                source="runtime",
            )
        return thread.id, goal.id
    finally:
        application.close()


def test_loop_cli_completes_through_the_model_goal_tool(monkeypatch, tmp_path, capsys):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_calc.py").write_text(
        textwrap.dedent(
            """
            from calc import add
            def test_add():
                assert add(2, 3) == 5
            """
        )
    )
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)
    rc = loop_cli.main(
        [
            "keep calc.add working",
            "-w",
            str(ws),
            "-t",
            "python -m pytest -q",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Goal active" in out
    assert "Goal complete" in out
    assert any(
        "python -m pytest -q" in str(request.get("messages"))
        for request in provider.requests
    )
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    sessions = SessionStore(tmp_path / "sessions")
    summary = sessions.list_sessions()[0]
    goal = ThreadGoalStore(sessions).read(summary.session_id)
    assert goal is not None
    assert goal.status is ThreadGoalStatus.COMPLETE


def test_loop_cli_returns_failure_for_a_model_reported_blocker(
    monkeypatch,
    tmp_path,
    capsys,
):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # buggy
    (ws / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    provider = _Provider("blocked")
    _configure(monkeypatch, tmp_path, provider)
    rc = loop_cli.main(["fix it", "-w", str(ws), "-t", "python -m pytest -q"])
    assert rc == 1
    assert "Goal blocked" in capsys.readouterr().out


def test_loop_resume_complete_goal_is_read_only(monkeypatch, tmp_path, capsys):
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)

    assert loop_cli.main(["ship it", "-w", str(workspace)]) == 0
    sessions = SessionStore(tmp_path / "sessions")
    session_id = sessions.list_sessions()[0].session_id
    goal_before = ThreadGoalStore(sessions).read(session_id)
    assert goal_before is not None
    transcript = sessions.root / session_id / "session.jsonl"
    ledger = sessions.root / session_id / "goal.jsonl"
    transcript_before = transcript.read_bytes()
    ledger_before = ledger.read_bytes()
    calls_before = provider.calls

    assert loop_cli.main(["--resume", session_id]) == 0

    summaries = sessions.list_sessions()
    assert [summary.session_id for summary in summaries] == [session_id]
    assert ThreadGoalStore(sessions).read(session_id) == goal_before
    assert transcript.read_bytes() == transcript_before
    assert ledger.read_bytes() == ledger_before
    assert provider.calls == calls_before
    assert "reason" in capsys.readouterr().out


def test_loop_resume_active_idle_goal_reuses_identity_and_stored_workspace(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "original-workspace"
    elsewhere = tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)
    session_id, goal_id = _create_idle_goal(workspace)
    session_before = SessionStore(tmp_path / "sessions").get_session(session_id)
    assert session_before is not None
    monkeypatch.chdir(elsewhere)

    assert loop_cli.main(["--resume", session_id]) == 0

    sessions = SessionStore(tmp_path / "sessions")
    resumed = ThreadGoalStore(sessions).read(session_id)
    session_after = sessions.get_session(session_id)
    assert resumed is not None
    assert resumed.id == goal_id
    assert resumed.status is ThreadGoalStatus.COMPLETE
    assert session_after is not None
    assert session_after.metadata["workspace"] == str(workspace.resolve())
    assert session_after.metadata["workspace"] == session_before.metadata["workspace"]
    assert workspace.name in capsys.readouterr().out


def test_loop_resume_blocked_goal_retries_without_new_session(
    monkeypatch,
    tmp_path,
):
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _Provider("blocked")
    _configure(monkeypatch, tmp_path, provider)
    assert loop_cli.main(["fix it", "-w", str(workspace)]) == 1
    sessions = SessionStore(tmp_path / "sessions")
    session_id = sessions.list_sessions()[0].session_id
    blocked = ThreadGoalStore(sessions).read(session_id)
    assert blocked is not None
    assert blocked.status is ThreadGoalStatus.BLOCKED

    provider.status = "complete"
    assert loop_cli.main(["--resume", session_id]) == 0

    summaries = sessions.list_sessions()
    resumed = ThreadGoalStore(sessions).read(session_id)
    assert [summary.session_id for summary in summaries] == [session_id]
    assert resumed is not None
    assert resumed.id == blocked.id
    assert resumed.status is ThreadGoalStatus.COMPLETE
    assert len(sessions.get_session(session_id).messages) > 2


def test_loop_resume_paused_goal_uses_the_same_goal(monkeypatch, tmp_path):
    from core.application import DeepCodeApplication
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)
    session_id, goal_id = _create_idle_goal(workspace)
    application = DeepCodeApplication.open()
    try:
        application.goals.pause(
            session_id,
            expected_goal_id=goal_id,
        )
    finally:
        application.close()

    assert loop_cli.main(["--resume", session_id]) == 0

    sessions = SessionStore(tmp_path / "sessions")
    resumed = ThreadGoalStore(sessions).read(session_id)
    assert resumed is not None
    assert resumed.id == goal_id
    assert resumed.status is ThreadGoalStatus.COMPLETE
    assert len(sessions.list_sessions()) == 1


def test_loop_resume_active_running_goal_attaches_without_duplicate_turn(
    monkeypatch,
    tmp_path,
):
    from core.application.goal_extension import (
        GoalContinueDisposition,
        GoalExtension,
    )
    from core.sessions import SessionStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _BlockingProvider()
    _configure(monkeypatch, tmp_path, provider)
    attached = threading.Event()
    original_continue = GoalExtension.continue_goal

    def observe_continue(self, *args, **kwargs):
        result = original_continue(self, *args, **kwargs)
        if result.disposition is GoalContinueDisposition.ALREADY_RUNNING:
            attached.set()
        return result

    monkeypatch.setattr(GoalExtension, "continue_goal", observe_continue)
    with ThreadPoolExecutor(max_workers=2) as executor:
        initial = executor.submit(
            loop_cli.main,
            ["finish once", "-w", str(workspace)],
        )
        assert provider.entered.wait(timeout=5)
        sessions = SessionStore(tmp_path / "sessions")
        summaries = sessions.list_sessions()
        assert len(summaries) == 1
        session_id = summaries[0].session_id

        resumed = executor.submit(loop_cli.main, ["--resume", session_id])
        assert attached.wait(timeout=5)
        provider.release.set()

        assert initial.result(timeout=10) == 0
        assert resumed.result(timeout=10) == 0

    assert provider.calls == 2
    assert len(SessionStore(tmp_path / "sessions").list_sessions()) == 1


def test_loop_waits_for_the_deciding_turn_to_finish(monkeypatch, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _BlockingFinalResponseProvider()
    _configure(monkeypatch, tmp_path, provider)

    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(
            loop_cli.main,
            ["finish cleanly", "-w", str(workspace)],
        )
        assert provider.final_started.wait(timeout=5)
        assert not running.done()
        provider.release_final.set()
        assert running.result(timeout=10) == 0

    assert provider.calls == 2


def test_loop_resume_budget_limited_requires_a_larger_budget(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.domain import ThreadGoalStatus
    from core.sessions import SessionStore, ThreadGoalStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)
    session_id, goal_id = _create_idle_goal(
        workspace,
        token_budget=10,
        tokens_used=10,
    )

    assert loop_cli.main(["--resume", session_id]) == 1
    output = capsys.readouterr().out
    assert "provide a larger" in output
    assert "--token-budget to resume" in output
    assert provider.calls == 0

    assert loop_cli.main(["--resume", session_id, "--token-budget", "100"]) == 0

    sessions = SessionStore(tmp_path / "sessions")
    resumed = ThreadGoalStore(sessions).read(session_id)
    assert resumed is not None
    assert resumed.id == goal_id
    assert resumed.token_budget == 100
    assert resumed.status is ThreadGoalStatus.COMPLETE
    assert len(sessions.list_sessions()) == 1


def test_loop_resume_execution_override_is_one_turn_only(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.sessions import SessionStore

    workspace = tmp_path / "project"
    workspace_override = tmp_path / "explicit-workspace"
    workspace.mkdir()
    workspace_override.mkdir()
    provider = _Provider("complete")
    provider_options = _configure(monkeypatch, tmp_path, provider)
    session_id, _goal_id = _create_idle_goal(workspace)
    sessions = SessionStore(tmp_path / "sessions")
    before = sessions.get_session(session_id)
    assert before is not None

    assert (
        loop_cli.main(
            [
                "--resume",
                session_id,
                "--workspace",
                str(workspace_override),
                "--connection",
                "openrouter",
                "--model",
                "next-model",
                "--effort",
                "high",
            ]
        )
        == 0
    )

    after = sessions.get_session(session_id)
    assert after is not None
    assert after.metadata.get("connection_id") == before.metadata.get("connection_id")
    assert after.metadata.get("model") == before.metadata.get("model")
    assert after.metadata.get("reasoning_effort") == before.metadata.get(
        "reasoning_effort"
    )
    assert after.metadata.get("workspace") == before.metadata.get("workspace")
    assert provider_options[-1]["connection_id"] == "openrouter"
    assert provider_options[-1]["model"] == "next-model"
    assert provider_options[-1]["execution_profile"].reasoning_effort == "high"
    assert workspace_override.name in capsys.readouterr().out


def test_loop_resume_rejects_missing_goal_and_mutating_creation_options(
    monkeypatch,
    tmp_path,
    capsys,
):
    from core.application import DeepCodeApplication
    from core.domain import TrustState
    from core.sessions import SessionStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = _Provider("complete")
    _configure(monkeypatch, tmp_path, provider)
    application = DeepCodeApplication.open()
    try:
        project = application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED,
        )
        session_id = application.threads.start(
            project.id,
            title="No Goal",
        ).id
    finally:
        application.close()

    assert loop_cli.main(["--resume", session_id]) == 1
    assert "no Goal is attached" in capsys.readouterr().out
    assert len(SessionStore(tmp_path / "sessions").list_sessions()) == 1
    with pytest.raises(SystemExit):
        loop_cli.main(["--resume", session_id, "--test-cmd", "pytest"])
    with pytest.raises(SystemExit):
        loop_cli.main(["--resume", session_id, "--skill", "review"])
