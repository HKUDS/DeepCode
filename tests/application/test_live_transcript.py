"""The session file follows a Turn as the model lives it.

Each message is written when it enters the model's history, in that order,
so a session resumed from the file gives the model the history it had: a
Steer where it was read, a sub-agent result after the call that waited for
it, and the work of a Turn that was stopped, failed or cut off by a crash.
"""

from __future__ import annotations

import time
from pathlib import Path

from core.domain.message_provenance import ClientSurface
from core.domain.turn import TurnStatus
from core.providers.base import LLMResponse
from tests.application.test_transcript_projection import (
    _application,
    _calls_wait,
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
