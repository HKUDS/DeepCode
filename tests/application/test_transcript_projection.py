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

from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.message_provenance import ClientSurface
from core.domain.turn import TurnStatus
from core.events import AgentSession
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.providers.base import LLMResponse, ToolCallRequest
from core.sessions import SessionStore


class _Model:
    """Answers from a script: ``None`` never returns, an exception raises."""

    def __init__(self, script: list[LLMResponse | Exception | None]) -> None:
        self.script = list(script)
        self.called = threading.Event()
        self.requests: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "scripted"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append([dict(message) for message in kwargs["messages"]])
        self.called.set()
        response = self.script.pop(0)
        if response is None:
            await asyncio.Event().wait()
        if isinstance(response, Exception):
            raise response
        return response


@tool_parameters({"type": "object", "properties": {}})
class _Wait(Tool):
    """A tool that returns once its gate opens (never, unless a test opens it)."""

    def __init__(self) -> None:
        self.gate = threading.Event()

    @property
    def name(self) -> str:
        return "wait"

    @property
    def description(self) -> str:
        return "Wait for the test."

    async def execute(self, **_kwargs: Any) -> Any:
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        return "waited"


@tool_parameters({"type": "object", "properties": {}})
class _ReadLog(Tool):
    """A tool with a result large enough for the pruner to shorten."""

    @property
    def name(self) -> str:
        return "read_log"

    @property
    def description(self) -> str:
        return "Read a long log."

    async def execute(self, **_kwargs: Any) -> Any:
        return " ".join(f"line{index}" for index in range(2000))


def _calls(tool: str, text: str = "") -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[ToolCallRequest(id=f"call-{tool}", name=tool, arguments={})],
        finish_reason="tool_calls",
    )


def _calls_wait(text: str) -> LLMResponse:
    return _calls("wait", text)


class _Factory:
    def __init__(self, model: _Model) -> None:
        self.model = model
        self.wait = _Wait()
        # Asks the model to go on once it would stop, as Goal turns do.
        self.closure_callback = None

    def create(
        self,
        *,
        workspace,
        model,
        approval_callback,
        injection_callback=None,
        context_note_sink=None,
        history_sink=None,
    ) -> AgentSession:
        tools = ToolRegistry()
        tools.register(self.wait)
        tools.register(_ReadLog())
        return AgentSession(
            self.model,
            tools,
            model="scripted",
            workspace=workspace,
            approval_callback=approval_callback,
            injection_callback=injection_callback,
            context_note_sink=context_note_sink,
            history_sink=history_sink,
            closure_callback=self.closure_callback,
            streaming=True,
        )


def _application(
    tmp_path: Path, *script: LLMResponse | Exception | None
) -> tuple[DeepCodeApplication, str, _Factory]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    factory = _Factory(_Model(list(script)))
    application = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", session_factory=factory
    )
    project = application.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = application.threads.start(project.id, title="Projection")
    return application, thread.id, factory


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


def _wait_until_said(application, turn_id: str, text: str) -> None:
    deadline = time.monotonic() + 5
    while not any(
        item.payload.get("text") == text
        for item in application.turns.read(turn_id).items
    ):
        assert time.monotonic() < deadline, f"the model never said {text!r}"
        time.sleep(0.01)


def _written_elsewhere(application, thread_id: str) -> None:
    """A later exchange that only the session file has, e.g. from another tool."""

    store = application.session_store
    store.append_message(thread_id, "user", "written elsewhere")
    store.append_message(thread_id, "assistant", "read here")


def test_the_interruption_marker_is_not_a_turn_of_its_own(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path, None, LLMResponse(content="answered")
    )
    try:
        first = application.turns.start(thread_id, prompt="first")
        _interrupt(application, thread_id, first.turn.id, factory.model)
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


def test_steering_after_the_model_spoke_keeps_the_projection_in_step(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check."),
        LLMResponse(content="Checked, and did X too."),
        LLMResponse(content="answered"),
    )
    try:
        first = application.turns.start(thread_id, prompt="first")
        _wait_until_said(application, first.turn.id, "Let me check.")
        application.turns.steer(
            thread_id,
            expected_turn_id=first.turn.id,
            prompt="also do X",
            message_id="steer-1",
            client_surface=ClientSurface.WEB,
        )
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert not _conflicted(application, thread_id)
        _written_elsewhere(application, thread_id)
        assert _projected_prompts(application, thread_id) == [
            "first",
            "second",
            "written elsewhere",
        ]
    finally:
        application.close()


def test_what_the_model_said_before_an_interrupt_keeps_the_projection_in_step(
    tmp_path: Path,
) -> None:
    application, thread_id, _factory = _application(
        tmp_path, _calls_wait("Let me check."), LLMResponse(content="answered")
    )
    try:
        first = application.turns.start(thread_id, prompt="first")
        _wait_until_said(application, first.turn.id, "Let me check.")
        application.turns.interrupt(thread_id, first.turn.id)
        _wait_for(application, first.turn.id, TurnStatus.INTERRUPTED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert not _conflicted(application, thread_id)
        _written_elsewhere(application, thread_id)
        assert _projected_prompts(application, thread_id) == [
            "first",
            "second",
            "written elsewhere",
        ]
    finally:
        application.close()


def test_what_the_model_said_before_a_failure_keeps_the_projection_in_step(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check."),
        RuntimeError("connection reset"),
        LLMResponse(content="answered"),
    )
    factory.wait.gate.set()
    try:
        first = application.turns.start(thread_id, prompt="first")
        _wait_for(application, first.turn.id, TurnStatus.FAILED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert not _conflicted(application, thread_id)
        _written_elsewhere(application, thread_id)
        assert _projected_prompts(application, thread_id) == [
            "first",
            "second",
            "written elsewhere",
        ]
    finally:
        application.close()


def _transcript(application, thread_id: str) -> list[tuple[str, str, str | None]]:
    session = application.session_store.get_session(thread_id)
    assert session is not None
    return [
        (message.role, message.content, (message.metadata or {}).get("turnId"))
        for message in session.messages
    ]


class _ReplyWrites:
    """Makes saving the model's replies to the session file fail on demand."""

    def __init__(self, application: DeepCodeApplication, monkeypatch) -> None:
        self.failures_left = 0
        store = application.session_store
        append = store.append_message

        def append_message(session_id, role, content, **kwargs):
            if role in {"assistant", "tool"} and self.failures_left:
                self.failures_left -= 1
                raise OSError("No space left on device")
            return append(session_id, role, content, **kwargs)

        monkeypatch.setattr(store, "append_message", append_message)


def test_a_record_that_could_not_be_written_is_written_with_the_next(
    tmp_path: Path, monkeypatch
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check."),
        LLMResponse(content="checked"),
        LLMResponse(content="answered"),
    )
    factory.wait.gate.set()
    writes = _ReplyWrites(application, monkeypatch)
    writes.failures_left = 1
    try:
        # Writing the call fails once; the Turn goes on, and the next write
        # puts it on record, in its place and under its Turn.
        first = application.turns.start(thread_id, prompt="first")
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        one, two = first.turn.id, second.turn.id
        assert _transcript(application, thread_id) == [
            ("user", "first", one),
            ("assistant", "Let me check.", one),
            ("tool", "waited", one),
            ("assistant", "checked", one),
            ("user", "second", two),
            ("assistant", "answered", two),
        ]
    finally:
        application.close()


def test_what_a_failed_turn_could_not_save_is_not_filed_under_the_next(
    tmp_path: Path, monkeypatch
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check."),
        LLMResponse(content="checked"),
        LLMResponse(content="answered"),
    )
    factory.wait.gate.set()
    writes = _ReplyWrites(application, monkeypatch)
    writes.failures_left = 100
    try:
        first = application.turns.start(thread_id, prompt="first")
        _wait_for(application, first.turn.id, TurnStatus.FAILED)
        writes.failures_left = 0
        second = application.turns.start(thread_id, prompt="second")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert _transcript(application, thread_id) == [
            ("user", "first", first.turn.id),
            ("user", "second", second.turn.id),
            ("assistant", "answered", second.turn.id),
        ]
    finally:
        application.close()


def test_a_reply_the_model_gave_before_is_saved_again(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        LLMResponse(content="Working on it."),
        LLMResponse(content="Step one is done."),
        LLMResponse(content="Working on it."),
        LLMResponse(content="Step two is done."),
    )
    factory.closure_callback = lambda asked_already: (
        None if asked_already else "Say whether the goal is complete."
    )
    try:
        turns = []
        for prompt in ("first", "second"):
            started = application.turns.start(thread_id, prompt=prompt)
            _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
            turns.append(started.turn.id)

        replies = [
            (content, turn_id)
            for role, content, turn_id in _transcript(application, thread_id)
            if role == "assistant"
        ]
        assert replies == [
            ("Working on it.", turns[0]),
            ("Step one is done.", turns[0]),
            ("Working on it.", turns[1]),
            ("Step two is done.", turns[1]),
        ]
    finally:
        application.close()


def test_a_reply_compaction_folded_away_is_saved_when_it_repeats(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        LLMResponse(content="Working on it."),
        LLMResponse(content="Step one is done."),
        LLMResponse(content="two " * 400),
        LLMResponse(content="Step two is done."),
        LLMResponse(content="Summary of the work so far."),
        LLMResponse(content="Working on it."),
        LLMResponse(content="Step three is done."),
    )
    factory.closure_callback = lambda asked_already: (
        None if asked_already else "Say whether the goal is complete."
    )
    try:
        for prompt in ("first", "second"):
            started = application.turns.start(thread_id, prompt=prompt)
            _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
        # `/compact` folds the first turn, and its "Working on it.", away.
        application.turns.run_compaction(thread_id)
        third = application.turns.start(thread_id, prompt="third")
        _wait_for(application, third.turn.id, TurnStatus.COMPLETED)

        replies = [
            content
            for role, content, turn_id in _transcript(application, thread_id)
            if role == "assistant" and turn_id == third.turn.id
        ]
        assert replies == ["Working on it.", "Step three is done."]
    finally:
        application.close()


def test_a_tool_result_shortened_under_context_pressure_is_not_saved_again(
    tmp_path: Path,
) -> None:
    application, thread_id, _factory = _application(
        tmp_path,
        _calls("read_log"),
        LLMResponse(content="Read it."),
        # The provider refuses the next request as too long; the runner
        # shortens the old log result in the history and tries again.
        LLMResponse(
            content="", finish_reason="error", error_code="context_length_exceeded"
        ),
        LLMResponse(content="Done."),
    )
    try:
        for prompt in ("first", "second"):
            started = application.turns.start(thread_id, prompt=prompt)
            _wait_for(application, started.turn.id, TurnStatus.COMPLETED)

        results = [
            content
            for role, content, _turn_id in _transcript(application, thread_id)
            if role == "tool"
        ]
        assert len(results) == 1
        assert results[0].endswith("line1999")
    finally:
        application.close()
