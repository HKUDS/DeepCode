from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from core.application import DeepCodeApplication
from core.application.errors import ConflictError
from core.domain import TrustState
from core.domain.execution_profile import ExecutionProfile
from core.domain.turn import TurnStatus
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from core.sessions import SessionStore


class ProfileSession:
    def __init__(self, *, hang: bool) -> None:
        self.hang = hang
        self.history: list[dict[str, Any]] = []
        self.closed = False

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = list(messages)

    async def run_stream(self, op):
        self.history.append({"role": "user", "content": op.text})
        yield Event("1", TurnStarted())
        if self.hang:
            await asyncio.Event().wait()
        answer = f"completed: {op.text}"
        yield Event("2", AgentMessage(answer))
        yield Event("3", TaskComplete(answer, "completed"))
        self.history.append({"role": "assistant", "content": answer})

    async def aclose(self) -> None:
        self.closed = True


class ProfileFactory:
    def __init__(self, *, hang_first: bool = False) -> None:
        self.hang_first = hang_first
        self.sessions: list[ProfileSession] = []
        self.profiles: list[ExecutionProfile] = []

    def create(
        self,
        *,
        workspace,
        model,
        execution_profile,
        approval_callback,
    ):
        del workspace, model, approval_callback
        self.profiles.append(execution_profile)
        session = ProfileSession(
            hang=self.hang_first and not self.sessions,
        )
        self.sessions.append(session)
        return session


def _write_connections(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "connection": "router-a",
                        "model": "moonshotai/kimi-k2.5",
                    }
                },
                "providers": {
                    "profiles": {
                        "router-a": {
                            "label": "Router A",
                            "template": "openrouter",
                        },
                        "router-b": {
                            "label": "Router B",
                            "template": "openrouter",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: ProfileFactory,
) -> tuple[DeepCodeApplication, str, SessionStore]:
    home = tmp_path / "home"
    _write_connections(home)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = SessionStore(tmp_path / "sessions")
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
        session_store=sessions,
    )
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    thread = application.threads.start(
        project.id,
        title="Model switching",
        connection_id="router-a",
        model="moonshotai/kimi-k2.5",
    )
    return application, thread.id, sessions


def _wait(
    application: DeepCodeApplication,
    turn_id: str,
    status: TurnStatus,
):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"turn did not reach {status}: {application.turns.read(turn_id).turn}"
    )


def test_session_switch_rebuilds_runtime_but_preserves_history_and_turn_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = ProfileFactory()
    application, thread_id, sessions = _application(tmp_path, monkeypatch, factory)
    try:
        first = application.turns.start(thread_id, prompt="first")
        first_done = _wait(
            application,
            first.turn.id,
            TurnStatus.COMPLETED,
        )
        application.threads.set_execution_selection(
            thread_id,
            connection_id="router-b",
            model="openai/gpt-5-mini",
        )
        second = application.turns.start(thread_id, prompt="second")
        second_done = _wait(
            application,
            second.turn.id,
            TurnStatus.COMPLETED,
        )

        assert first_done.turn.execution_profile is not None
        assert second_done.turn.execution_profile is not None
        assert first_done.turn.execution_profile.connection_id == "router-a"
        assert first_done.turn.execution_profile.model_id == "moonshotai/kimi-k2.5"
        assert second_done.turn.execution_profile.connection_id == "router-b"
        assert second_done.turn.execution_profile.model_id == "openai/gpt-5-mini"
        assert [profile.connection_id for profile in factory.profiles] == [
            "router-a",
            "router-b",
        ]
        assert factory.sessions[0].closed is True
        assert factory.sessions[1].history == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "completed: first"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "completed: second"},
        ]

        canonical = sessions.get_session(thread_id)
        assert canonical is not None
        assert canonical.metadata["connection_id"] == "router-b"
        assert canonical.metadata["model"] == "openai/gpt-5-mini"
        assert canonical.messages[0].metadata["executionProfile"][
            "connectionId"
        ] == "router-a"
        assert canonical.messages[-1].metadata["executionProfile"][
            "connectionId"
        ] == "router-b"

        original_retry = application.turns.retry(first.turn.id)
        original_done = _wait(
            application,
            original_retry.turn.id,
            TurnStatus.COMPLETED,
        )
        assert original_done.turn.execution_profile == first_done.turn.execution_profile

        current_retry = application.turns.retry(
            first.turn.id,
            use_current_selection=True,
        )
        current_done = _wait(
            application,
            current_retry.turn.id,
            TurnStatus.COMPLETED,
        )
        assert current_done.turn.execution_profile is not None
        assert current_done.turn.execution_profile.connection_id == "router-b"
        assert current_done.turn.execution_profile.model_id == "openai/gpt-5-mini"
    finally:
        application.close()

def test_queued_turn_keeps_override_and_active_turn_blocks_session_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = ProfileFactory(hang_first=True)
    application, thread_id, _sessions = _application(tmp_path, monkeypatch, factory)
    try:
        active = application.turns.start(thread_id, prompt="active")
        _wait(application, active.turn.id, TurnStatus.RUNNING)
        queued = application.turns.enqueue(
            thread_id,
            prompt="queued",
            connection_id="router-b",
            model="openai/gpt-5-mini",
        )

        assert queued.turn.status is TurnStatus.QUEUED
        assert queued.turn.execution_profile is not None
        assert queued.turn.execution_profile.connection_id == "router-b"
        assert queued.turn.execution_profile.model_id == "openai/gpt-5-mini"
        with pytest.raises(ConflictError, match="cannot change while a Turn is active"):
            application.threads.set_execution_selection(
                thread_id,
                connection_id="router-b",
                model="openai/gpt-5-mini",
            )

        application.turns.interrupt(active.turn.id)
        _wait(application, active.turn.id, TurnStatus.INTERRUPTED)
        queued_done = _wait(application, queued.turn.id, TurnStatus.COMPLETED)

        assert queued_done.turn.execution_profile == queued.turn.execution_profile
        assert [profile.connection_id for profile in factory.profiles] == [
            "router-a",
            "router-b",
        ]
    finally:
        application.close()
