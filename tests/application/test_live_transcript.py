"""The session file follows a Turn as the model lives it.

Each message is written when it enters the model's history, in that order,
so a session resumed from the file gives the model the history it had: a
Steer where it was read, a sub-agent result after the call that waited for
it, and the work of a Turn that was stopped, failed or cut off by a crash.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from core.agent_runtime.context import EnvironmentContext
from core.agent_runtime.injections import SubagentMessage
from core.application import DeepCodeApplication
from core.domain import TrustState
from core.domain.message_provenance import ClientSurface
from core.domain.turn import TurnStatus
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import TurnRepository
from core.providers.base import LLMResponse
from core.sessions import SessionStore
from core.sessions.transcript import visible_kernel_history
from tests.application.test_transcript_projection import (
    _application,
    _calls,
    _calls_wait,
    _Factory,
    _Model,
    _transcript,
    _wait_for,
    _wait_until_said,
)


def _steer(application, thread_id: str, turn_id: str, text: str) -> None:
    application.turns.steer(
        thread_id,
        expected_turn_id=turn_id,
        prompt=text,
        message_id="steer-1",
        client_surface=ClientSurface.WEB,
    )


def _steer_records(application, thread_id: str) -> list:
    session = application.session_store.get_session(thread_id)
    assert session is not None
    return [
        message
        for message in session.messages
        if (message.metadata or {}).get("source") == "steer"
    ]


def _wait_until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


def test_a_steer_is_written_when_the_turn_reads_it(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Checked X, and did Y."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        _steer(application, thread_id, first.turn.id, "Also do Y.")
        # Accepted, but the Turn is still in its tool and has not read it.
        assert (
            application.turns.read_input(thread_id, "steer-1").payload["deliveryState"]
            == "accepted"
        )
        assert _steer_records(application, thread_id) == []

        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)

        [record] = _steer_records(application, thread_id)
        assert record.content == "Also do Y."
        assert record.metadata["messageId"] == "steer-1"
        assert record.metadata["client"] == "web"
        assert record.metadata["turnId"] == first.turn.id
        assert record.metadata["deliveryState"] == "accepted"
    finally:
        application.close()


def test_a_steer_the_turn_never_read_is_not_in_the_session(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Answered B."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        _steer(application, thread_id, first.turn.id, "Also do Y.")
        application.turns.interrupt(thread_id, first.turn.id)
        _wait_for(application, first.turn.id, TurnStatus.INTERRUPTED)

        second = application.turns.start(thread_id, prompt="Do B.")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        # Stopped before the Turn read it: the model never saw it, and does
        # not see it later either. Its receipt still says it was accepted.
        assert _steer_records(application, thread_id) == []
        assert not any(
            "Also do Y." in str(message.get("content"))
            for message in factory.model.requests[-1]
        )
        assert (
            application.turns.read_input(thread_id, "steer-1").payload["deliveryState"]
            == "accepted"
        )
    finally:
        application.close()


def _conversation(request: list[dict]) -> list[tuple[str, str]]:
    """A model request without the per-request system and environment parts."""

    return [
        (
            str(message.get("role")),
            "call"
            if message.get("tool_calls")
            else str(message.get("content")).splitlines()[0],
        )
        for message in request
        if message.get("role") != "system"
        and not EnvironmentContext.is_history_message(message)
    ]


def _assert_valid_for_providers(request: list[dict]) -> None:
    """Every tool call is answered right after it, and nothing else answers."""

    pending: set[str] = set()
    for message in request:
        if message.get("role") == "tool":
            assert message.get("tool_call_id") in pending, message
            pending.discard(message.get("tool_call_id"))
            continue
        assert not pending, f"{message} interrupts unanswered calls {pending}"
        if message.get("role") == "assistant" and message.get("tool_calls"):
            pending = {call["id"] for call in message["tool_calls"]}
    assert not pending


def _reopen(tmp_path: Path, *script) -> tuple[DeepCodeApplication, _Factory]:
    factory = _Factory(_Model(list(script)))
    return (
        DeepCodeApplication.open(tmp_path / "state.sqlite3", session_factory=factory),
        factory,
    )


def test_a_resumed_session_reads_in_the_order_the_model_did(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Checked X, and did Y."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        _steer(application, thread_id, first.turn.id, "Also do Y.")
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        lived = _conversation(factory.model.requests[-1])
    finally:
        application.close()

    resumed, factory = _reopen(tmp_path, LLMResponse(content="Did B."))
    try:
        second = resumed.turns.start(thread_id, prompt="Do B.")
        _wait_for(resumed, second.turn.id, TurnStatus.COMPLETED)

        assert lived == [
            ("user", "Do A."),
            ("assistant", "call"),
            ("tool", "waited"),
            ("user", "Also do Y."),
        ]
        assert _conversation(factory.model.requests[-1]) == [
            *lived,
            ("assistant", "Checked X, and did Y."),
            ("user", "Do B."),
        ]
    finally:
        resumed.close()


def test_a_crash_during_a_tool_leaves_its_call_on_record(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path, _calls_wait("Let me check X.")
    )
    crashed = tmp_path / "crashed"
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        # What a crash leaves while the tool runs: the file as it is now.
        session_file = application.session_store.root / thread_id / "session.jsonl"
        (crashed / thread_id).mkdir(parents=True)
        shutil.copy(session_file, crashed / thread_id / "session.jsonl")
    finally:
        factory.wait.gate.set()
        application.close()

    factory = _Factory(_Model([LLMResponse(content="Did B.")]))
    resumed = DeepCodeApplication.open(
        tmp_path / "resumed.sqlite3",
        session_factory=factory,
        session_store=SessionStore(crashed),
    )
    try:
        project = resumed.projects.add(str(tmp_path / "workspace"))
        resumed.projects.update(project.id, trust_state=TrustState.TRUSTED)
        second = resumed.turns.start(thread_id, prompt="Do B.")
        _wait_for(resumed, second.turn.id, TurnStatus.COMPLETED)

        request = factory.model.requests[-1]
        assert _conversation(request) == [
            ("user", "Do A."),
            ("assistant", "call"),
            # The call never answered is answered as interrupted.
            ("tool", "[Tool result unavailable — call was interrupted or lost]"),
            ("user", "Do B."),
        ]
        _assert_valid_for_providers(request)
    finally:
        resumed.close()


def test_a_sub_agent_result_follows_the_call_that_waited_for_it(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Waiting for the worker."),
        LLMResponse(content="The worker finished."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Waiting for the worker.")
        assert application.turns.session_runtimes.inject_transient(
            thread_id,
            SubagentMessage(
                message_id="result-1",
                target_turn_id=first.turn.id,
                agent_id="worker",
                payload="worker finished\n",
            ),
        )
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)

        assert [
            (role, content.splitlines()[0])
            for role, content, _id in _transcript(application, thread_id)
        ] == [
            ("user", "Do A."),
            ("assistant", "Waiting for the worker."),
            ("tool", "waited"),
            ("user", "Message Type: SUBAGENT_MESSAGE"),
            ("assistant", "The worker finished."),
        ]
    finally:
        application.close()


def test_stop_keeps_what_the_model_did_for_the_next_turn(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Did B."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        application.turns.interrupt(thread_id, first.turn.id)
        _wait_for(application, first.turn.id, TurnStatus.INTERRUPTED)
        second = application.turns.start(thread_id, prompt="Do B.")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        request = factory.model.requests[-1]
        assert _conversation(request) == [
            ("user", "Do A."),
            ("assistant", "call"),
            ("tool", "[Tool result unavailable — call was interrupted or lost]"),
            ("user", "[The previous Turn was interrupted before it completed.]"),
            ("user", "Do B."),
        ]
        _assert_valid_for_providers(request)
    finally:
        application.close()


def test_a_failure_keeps_what_the_model_did_for_the_next_turn(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        RuntimeError("connection reset"),
        LLMResponse(content="Did B."),
    )
    factory.wait.gate.set()
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_for(application, first.turn.id, TurnStatus.FAILED)
        second = application.turns.start(thread_id, prompt="Do B.")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)

        assert _conversation(factory.model.requests[-1]) == [
            ("user", "Do A."),
            ("assistant", "call"),
            ("tool", "waited"),
            ("user", "Do B."),
        ]
    finally:
        application.close()


def _rebuilt(tmp_path: Path, application, thread_id: str, name: str):
    """A projection rebuilt from the session file alone, in a fresh database."""

    rebuilt = DeepCodeApplication.open(
        tmp_path / f"{name}.sqlite3",
        session_store=SessionStore(application.session_store.root),
    )
    rebuilt.threads.read(thread_id)
    return rebuilt


def _projection(application, thread_id: str) -> tuple[list, list, bool]:
    application.threads.read(thread_id)
    with application.database.read() as connection:
        turns = TurnRepository(connection).list_for_thread(thread_id)
        items = connection.execute(
            "SELECT kind, payload_json FROM items WHERE thread_id = ? "
            "ORDER BY created_at, ordinal",
            (thread_id,),
        ).fetchall()
        conflicted = EventRepository(connection).has_type(
            thread_id, "thread.projection_conflict"
        )
    return [turn.prompt for turn in turns], [tuple(row) for row in items], conflicted


def test_a_rebuilt_projection_keeps_a_steer_in_its_turn(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Checked X, and did Y."),
        LLMResponse(content="Did B."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        _steer(application, thread_id, first.turn.id, "Also do Y.")
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        second = application.turns.start(thread_id, prompt="Do B.")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)
        assert _projection(application, thread_id)[0] == ["Do A.", "Do B."]
    finally:
        application.close()

    rebuilt = _rebuilt(tmp_path, application, thread_id, "rebuilt")
    try:
        prompts, items, conflicted = _projection(rebuilt, thread_id)
        assert prompts == ["Do A.", "Do B."]
        assert not conflicted
        assert _projection(rebuilt, thread_id)[1] == items
        steer = rebuilt.turns.read_input(thread_id, "steer-1")
        assert steer.payload["text"] == "Also do Y."
        assert steer.payload["deliveryState"] == "accepted"
    finally:
        rebuilt.close()


def test_reading_a_thread_mid_turn_projects_nothing_twice(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Let me check X."),
        LLMResponse(content="Checked X."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Let me check X.")
        _wait_until(lambda: len(_transcript(application, thread_id)) == 2)
        # The file already holds the call; the live projection shows it too.
        assert _projection(application, thread_id)[0] == ["Do A."]
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)
        prompts, items, conflicted = _projection(application, thread_id)
        assert prompts == ["Do A."]
        assert not conflicted
        assert _projection(application, thread_id)[1] == items
    finally:
        application.close()


def test_a_turn_compacted_midway_rebuilds_and_resumes_cleanly(tmp_path: Path) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        *(
            _calls("read_page", f"Reading page {page}.", call_id=f"page-{page}")
            for page in range(1, 6)
        ),
        LLMResponse(content="Read them all."),
        LLMResponse(content="Did B."),
    )
    factory.model.summary = "Summary: pages 1 to 4 read."
    factory.context_window_tokens = 9000
    try:
        for prompt in ("Do A.", "Do B."):
            started = application.turns.start(thread_id, prompt=prompt)
            _wait_for(application, started.turn.id, TurnStatus.COMPLETED)
        session = application.session_store.get_session(thread_id)
        assert any(
            (message.metadata or {}).get("compaction") for message in session.messages
        ), "the turn was expected to compact midway"
        # Nothing is on record twice: one record per call and per result.
        calls = [
            call["id"]
            for message in session.messages
            for call in (message.metadata or {}).get("toolCalls") or ()
        ]
        results = [
            message.metadata["toolCallId"]
            for message in session.messages
            if message.role == "tool"
        ]
        assert len(calls) == len(set(calls)) == 5
        assert sorted(results) == sorted(calls)
        prompts, _items, conflicted = _projection(application, thread_id)
        assert prompts == ["Do A.", "Do B."]
        assert not conflicted
    finally:
        application.close()

    rebuilt = _rebuilt(tmp_path, application, thread_id, "rebuilt")
    try:
        prompts, items, conflicted = _projection(rebuilt, thread_id)
        assert prompts == ["Do A.", "Do B."]
        assert not conflicted
        assert _projection(rebuilt, thread_id)[1] == items
    finally:
        rebuilt.close()

    resumed, factory = _reopen(tmp_path, LLMResponse(content="Did C."))
    try:
        third = resumed.turns.start(thread_id, prompt="Do C.")
        _wait_for(resumed, third.turn.id, TurnStatus.COMPLETED)
        request = factory.model.requests[-1]
        conversation = _conversation(request)
        # The summary, what the compaction kept, and everything after it.
        assert conversation[0][1].startswith("This is your own earlier conversation")
        assert conversation[-4:] == [
            ("assistant", "Read them all."),
            ("user", "Do B."),
            ("assistant", "Did B."),
            ("user", "Do C."),
        ]
        _assert_valid_for_providers(request)
    finally:
        resumed.close()


def test_a_result_the_full_mailbox_refused_waits_for_the_turn_to_end(
    tmp_path: Path,
) -> None:
    application, thread_id, factory = _application(
        tmp_path,
        _calls_wait("Waiting for the workers."),
        LLMResponse(content="All workers done."),
        LLMResponse(content="Did B."),
    )
    try:
        first = application.turns.start(thread_id, prompt="Do A.")
        _wait_until_said(application, first.turn.id, "Waiting for the workers.")
        runtimes = application.turns.session_runtimes

        def result(index: int) -> SubagentMessage:
            return SubagentMessage(
                message_id=f"result-{index}",
                target_turn_id=first.turn.id,
                agent_id=f"worker-{index}",
                payload=f"worker {index} finished",
            )

        # The mailbox fills while the tool runs; the next result is refused,
        # and the sub-agent control writes it to the session instead.
        assert all(runtimes.inject_transient(thread_id, result(i)) for i in range(64))
        assert not runtimes.inject_transient(thread_id, result(64))
        factory.note_sink("worker 64 finished", "subagent", already_in_history=False)
        factory.wait.gate.set()
        _wait_for(application, first.turn.id, TurnStatus.COMPLETED)

        session = application.session_store.get_session(thread_id)
        assert session is not None
        _assert_valid_for_providers(visible_kernel_history(session.messages))

        second = application.turns.start(thread_id, prompt="Do B.")
        _wait_for(application, second.turn.id, TurnStatus.COMPLETED)
        request = factory.model.requests[-1]
        _assert_valid_for_providers(request)
        # The model reads it after the Turn it arrived in, before the next.
        assert _conversation(request)[-3:] == [
            ("assistant", "All workers done."),
            ("user", "worker 64 finished"),
            ("user", "Do B."),
        ]
    finally:
        application.close()
