"""A failed database write costs what clients see, never the Turn.

SQLite can refuse a write: "database is locked" once the busy timeout runs
out, or a disk I/O error. A failed write of streamed text used to fail the
whole Turn. A failure while saving the Turn's error or its interruption
skipped the Turn's finish, so the Turn stayed running and every later prompt
on its Session waited in the queue until the service restarted.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.application import DeepCodeApplication
from core.application import turn_service as turn_service_module
from core.application.turn_projection import TurnEventProjector
from core.domain import TrustState
from core.domain.item import ItemKind, ItemStatus
from core.domain.turn import TurnStatus
from core.events import (
    AgentMessage,
    AgentMessageDelta,
    Event,
    TaskComplete,
    TurnStarted,
)


class _Session:
    def __init__(self, scripts) -> None:
        self.scripts = scripts
        self.history: list[dict[str, Any]] = []

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        self.history = messages

    async def run_stream(self, _op):
        self.history.append({"role": "user", "content": _op.text})
        async for event in self.scripts.pop(0)():
            yield event

    async def aclose(self) -> None:
        return None


class _Factory:
    def __init__(self, scripts) -> None:
        self.scripts = scripts

    def create(self, *, workspace, model, approval_callback):
        return _Session(self.scripts)


async def _answer():
    yield Event("1", TurnStarted())
    yield Event("2", AgentMessageDelta("Hello ", "msg-1"))
    yield Event("3", AgentMessageDelta("world", "msg-1"))
    yield Event("4", AgentMessage("Hello world", "msg-1"))
    yield Event("5", TaskComplete("Hello world", "completed"))


async def _crash():
    yield Event("1", TurnStarted())
    raise RuntimeError("model connection reset")


async def _hang():
    yield Event("1", TurnStarted())
    await asyncio.Event().wait()


def _application(tmp_path: Path, *scripts) -> tuple[DeepCodeApplication, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=_Factory(list(scripts))
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Write failures")
    return application, thread.id


def _wait_for(application, turn_id: str, status: TurnStatus, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = application.turns.read(turn_id)
        if snapshot.turn.status is status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"turn did not reach {status}: {application.turns.read(turn_id).turn}"
    )


def _locked(*_args, **_kwargs):
    raise sqlite3.OperationalError("database is locked")


def _fail_once(monkeypatch, owner, name: str) -> None:
    original = getattr(owner, name)
    failed = []

    def flaky(*args, **kwargs):
        if not failed:
            failed.append(True)
            _locked()
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, flaky)


def _assert_next_turn_runs(application, thread_id: str) -> None:
    later = application.turns.start(thread_id, prompt="And again")
    _wait_for(application, later.turn.id, TurnStatus.COMPLETED)


def _last_assistant_message(application, thread_id: str) -> str:
    session = application.session_store.get_session(thread_id)
    assert session is not None
    return [m.content for m in session.messages if m.role == "assistant"][-1]


def test_a_failed_answer_write_keeps_the_turn_and_its_answer(tmp_path, monkeypatch):
    _fail_once(monkeypatch, TurnEventProjector, "_update_item")
    application, thread_id = _application(tmp_path, _answer, _answer)
    try:
        started = application.turns.start(thread_id, prompt="Say hello")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        answers = [i for i in snapshot.items if i.kind is ItemKind.ASSISTANT_MESSAGE]
        assert [(i.payload["text"], i.status) for i in answers] == [
            ("Hello world", ItemStatus.COMPLETED)
        ]
        notices = [i for i in snapshot.items if i.kind is ItemKind.ERROR]
        assert len(notices) == 1 and "may be incomplete" in notices[0].summary
        # A warning: the Turn it belongs to succeeded.
        assert notices[0].status is ItemStatus.COMPLETED
        assert notices[0].payload["severity"] == "warning"
        assert _last_assistant_message(application, thread_id) == "Hello world"
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_the_outcome_survives_when_streamed_text_cannot_be_saved(tmp_path, monkeypatch):
    # The answer's item is created, then its completion cannot be written.
    monkeypatch.setattr(TurnEventProjector, "_update_item", _locked)
    application, thread_id = _application(tmp_path, _answer, _answer)
    try:
        started = application.turns.start(thread_id, prompt="Say hello")
        snapshot = _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        assert snapshot.turn.stop_reason == "completed"
        answers = [i for i in snapshot.items if i.kind is ItemKind.ASSISTANT_MESSAGE]
        assert len(answers) == 1  # retried on the same item, never a second one
        assert [i.kind for i in snapshot.items].count(ItemKind.COMPLETION) == 1
        assert _last_assistant_message(application, thread_id) == "Hello world"
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_a_failed_turn_finishes_when_its_error_cannot_be_saved(tmp_path, monkeypatch):
    application, thread_id = _application(tmp_path, _crash, _answer)
    try:
        # No new item can be saved: not the error, not the completion.
        with monkeypatch.context() as patch:
            patch.setattr(TurnEventProjector, "_add_item", _locked)
            started = application.turns.start(thread_id, prompt="Try")
            failed = _wait_for(application, started.turn.id, TurnStatus.FAILED)

        assert failed.turn.error_message == "model connection reset"
        # The finish wrote the completion the projection could not.
        assert [i.kind for i in failed.items].count(ItemKind.COMPLETION) == 1
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_an_interrupted_turn_finishes_when_its_marker_cannot_be_saved(
    tmp_path, monkeypatch
):
    application, thread_id = _application(tmp_path, _hang, _answer)
    monkeypatch.setattr(application.turns, "_record_interruption_marker", _locked)
    try:
        started = application.turns.start(thread_id, prompt="Wait")
        _wait_for(application, started.turn.id, TurnStatus.RUNNING)
        application.turns.interrupt(thread_id, started.turn.id)
        _wait_for(application, started.turn.id, TurnStatus.INTERRUPTED)
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_finishing_a_turn_retries_a_locked_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        turn_service_module, "_FINISH_RETRY_DELAYS_S", (0.01,), raising=False
    )
    application, thread_id = _application(tmp_path, _answer, _answer)
    _fail_once(monkeypatch, application.turns, "_finish")
    try:
        started = application.turns.start(thread_id, prompt="Say hello")
        _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_a_turn_finishes_once_the_database_takes_the_write_again(tmp_path, monkeypatch):
    monkeypatch.setattr(
        turn_service_module, "_FINISH_RETRY_DELAYS_S", (0.01,), raising=False
    )
    application, thread_id = _application(tmp_path, _answer, _answer)
    finish = application.turns._finish
    refused = []

    def finish_after_a_while(turn_id, **kwargs):
        # The database keeps refusing for a while, not for a set number of tries.
        if len(refused) < 20:
            refused.append(turn_id)
            _locked()
        return finish(turn_id, **kwargs)

    monkeypatch.setattr(application.turns, "_finish", finish_after_a_while)
    try:
        started = application.turns.start(thread_id, prompt="Say hello")
        _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_a_turn_whose_outcome_cannot_be_saved_ends_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        turn_service_module, "_FINISH_RETRY_DELAYS_S", (0.01,), raising=False
    )
    application, thread_id = _application(tmp_path, _answer, _answer)
    finish = application.turns._finish

    def finish_only_as_failed(turn_id, **kwargs):
        # A fault that comes back on every attempt to record this outcome.
        if kwargs["status"] is not TurnStatus.FAILED:
            raise ValueError("cannot record this outcome")
        return finish(turn_id, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(application.turns, "_finish", finish_only_as_failed)
            started = application.turns.start(thread_id, prompt="Say hello")
            failed = _wait_for(application, started.turn.id, TurnStatus.FAILED)

        assert failed.turn.error_code == "TURN_FINISH_FAILED"
        assert "cannot record this outcome" in failed.turn.error_message
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()


def test_a_turn_that_could_not_start_is_finished_once_the_database_allows(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        turn_service_module, "_FINISH_RETRY_DELAYS_S", (0.01,), raising=False
    )
    application, thread_id = _application(tmp_path, _answer, _answer)
    start = application.executions.start

    def refuse_turns(job_id, job_factory, **kwargs):
        if job_id.startswith("turn_"):
            raise RuntimeError("the runtime refused the Turn")
        return start(job_id, job_factory, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(application.executions, "start", refuse_turns)
            _fail_once(patch, application.turns, "_finish")
            started = application.turns.start(thread_id, prompt="Say hello")
            failed = _wait_for(application, started.turn.id, TurnStatus.FAILED)

        assert failed.turn.stop_reason == "scheduler_error"
        _assert_next_turn_runs(application, thread_id)
    finally:
        application.close()
