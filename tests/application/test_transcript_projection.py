"""The canonical JSONL and the SQLite projection tell the same story.

Both are written while a Turn runs, but not at the same moments: the user's
prompt and steering reach JSONL when they arrive, the model's replies when the
Turn ends, and an interrupted Turn adds a marker the model reads next time.
These tests drive a real AgentSession (with a scripted model) through the
application and check what the projection makes of the result.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.registry import ToolRegistry
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.turn import TurnStatus
from core.events import AgentSession
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.providers.base import LLMResponse
from core.sessions import SessionStore


class _Model:
    """Answers from a script; ``None`` is a request that never returns."""

    def __init__(self, script: list[LLMResponse | None]) -> None:
        self.script = list(script)
        self.called = threading.Event()

    def get_default_model(self) -> str:
        return "scripted"

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        self.called.set()
        response = self.script.pop(0)
        if response is None:
            await asyncio.Event().wait()
        return response


class _Factory:
    def __init__(self, model: _Model) -> None:
        self.model = model

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        injection_callback=None,
        context_note_sink=None,
    ) -> AgentSession:
        return AgentSession(
            self.model,
            ToolRegistry(),
            model="scripted",
            workspace=workspace,
            approval_callback=approval_callback,
            injection_callback=injection_callback,
            context_note_sink=context_note_sink,
            streaming=True,
        )


def _application(
    tmp_path: Path, *script: LLMResponse | None
) -> tuple[DeepCodeApplication, str, _Model]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = _Model(list(script))
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=_Factory(model)
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Projection")
    return application, thread.id, model


def _wait_for(application: DeepCodeApplication, turn_id: str, status: TurnStatus):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"turn did not reach {status}: {application.turns.read(turn_id).turn}"
    )


def _projected_prompts(application: DeepCodeApplication, thread_id: str) -> list:
    application.threads.read(thread_id)
    with application.database.read() as connection:
        turns = TurnRepository(connection).list_for_thread(thread_id)
    return [turn.prompt for turn in turns]


def _conflicted(application: DeepCodeApplication, thread_id: str) -> bool:
    application.threads.read(thread_id)
    with application.database.read() as connection:
        return EventRepository(connection).has_type(
            thread_id, "thread.projection_conflict"
        )


def _interrupt(application, thread_id: str, turn_id: str, model: _Model) -> None:
    assert model.called.wait(5)
    application.turns.interrupt(thread_id, turn_id)
    _wait_for(application, turn_id, TurnStatus.INTERRUPTED)


def test_the_interruption_marker_is_not_a_turn_of_its_own(tmp_path: Path) -> None:
    application, thread_id, model = _application(
        tmp_path, None, LLMResponse(content="answered")
    )
    try:
        first = application.turns.start(thread_id, prompt="first")
        _interrupt(application, thread_id, first.turn.id, model)
        assert _projected_prompts(application, thread_id) == ["first"]

        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)
        assert _projected_prompts(application, thread_id) == ["first", "second"]
        assert not _conflicted(application, thread_id)
        sessions = application.session_store.root
    finally:
        application.close()

    # Rebuilt from JSONL alone, the marker still belongs to the Turn it ended.
    rebuilt = DeepCodeApplication.open(
        tmp_path / "rebuilt.sqlite3", session_store=SessionStore(sessions)
    )
    try:
        assert _projected_prompts(rebuilt, thread_id) == ["first", "second"]
        assert not _conflicted(rebuilt, thread_id)
    finally:
        rebuilt.close()
