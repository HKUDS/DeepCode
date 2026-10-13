"""The shared-service TUI survives service failures instead of exiting.

These drive the TUI pieces with fakes: a service that refuses requests, an
approval answered elsewhere, and service events the renderer cannot handle.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections import deque
from types import SimpleNamespace

from rich.console import Console

import cli.tui.app as tui_app
from cli.service_events import ServiceEventRenderer
from cli.service_thread_client import _REPLAY, ServiceThreadClient
from core.application.errors import ApplicationError
from core.domain.approval import ApprovalStatus
from core.domain.event import DomainEvent
from cli.tui.renderer import EventRenderer
from core.events import AgentMessageDelta, ErrorEvent, Event, TaskComplete

THREAD = "thread-1"
TURN = "turn_" + "a" * 26
ITEM = "item_" + "b" * 26


class _ServiceDown(ApplicationError):
    code = "SERVICE_UNAVAILABLE"


class _Client:
    runtime_mode = "service"

    def __init__(self) -> None:
        self.pending = None
        self.interrupt_error: Exception | None = None
        self.respond_error: Exception | None = None
        self.send_error: Exception | None = None
        self.sent: list[str] = []
        self.closed = False

    def interrupt(self):
        if self.interrupt_error is not None:
            raise self.interrupt_error
        return None

    def pending_approval(self):
        return self.pending

    def respond_to_approval(self, approval_id, decision):
        if self.respond_error is not None:
            raise self.respond_error
        return SimpleNamespace(status=decision)

    def send(self, text, *, skill_ids=()):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(text)
        return SimpleNamespace(kind="started")

    def set_event_loop(self, _loop):
        return None

    async def start_domain_events(self, _sink):
        return None

    async def close(self):
        self.closed = True


class _Renderer:
    def __init__(self) -> None:
        self.settled = 0

    def settle_turn(self):
        self.settled += 1


def _app(client: _Client) -> tuple[tui_app.TuiApp, io.StringIO]:
    app = object.__new__(tui_app.TuiApp)
    output = io.StringIO()
    app.console = Console(file=output, color_system=None, width=200)
    app.thread_client = client
    app.renderer = _Renderer()
    app._prompted_approval_ids = set()
    app._answered_approval_ids = set()
    app._last_send_delivered = True
    app.selected_skill_ids = []
    app.workspace = "."
    return app, output


def test_interrupt_failure_is_reported_not_raised() -> None:
    client = _Client()
    client.interrupt_error = _ServiceDown("The DeepCode service is not running.")
    app, _ = _app(client)

    assert app.stop_turn().startswith("Could not stop the turn:")
    assert asyncio.run(app.stop_turn_async()).startswith("Could not stop the turn:")


def test_late_approval_reply_is_not_sent_to_the_model() -> None:
    client = _Client()
    app, _ = _app(client)
    app._prompted_approval_ids.add("appr-1")

    status = app._respond_to_pending_approval("y")

    assert status is not None and "answered elsewhere" in status
    assert client.sent == []
    # Without an earlier prompt, a plain "yes" is an ordinary message.
    assert app._respond_to_pending_approval("yes") is None


def test_approval_already_resolved_reports_the_refusal() -> None:
    client = _Client()
    client.pending = SimpleNamespace(id="appr-1")
    client.respond_error = ApplicationError("approval already resolved")
    app, _ = _app(client)

    status = app._respond_to_pending_approval("y")

    assert status == "Could not answer the approval: approval already resolved"


def test_approval_resolved_elsewhere_is_announced_once() -> None:
    client = _Client()
    app, output = _app(client)
    app._prompted_approval_ids.add("appr-1")

    app._on_approval_resolved({"id": "appr-1", "status": "approved_once"})
    app._on_approval_resolved({"id": "appr-1", "status": "approved_once"})

    assert output.getvalue().count("Approval approved once elsewhere.") == 1


def test_own_approval_answer_is_not_announced_as_elsewhere() -> None:
    client = _Client()
    client.pending = SimpleNamespace(id="appr-1")
    app, output = _app(client)
    app._prompted_approval_ids.add("appr-1")

    assert app._respond_to_pending_approval("n") == "Approval denied."
    app._on_approval_resolved({"id": "appr-1", "status": ApprovalStatus.DENIED.value})

    assert "elsewhere" not in output.getvalue()


def test_repl_survives_a_failed_request_and_keeps_reading() -> None:
    client = _Client()
    # send_turn already reports ApplicationError; a transport timeout is a
    # plain RuntimeError and used to end the TUI with a traceback.
    client.send_error = RuntimeError("Service request timed out")
    app, output = _app(client)
    lines = iter(["first message", "second message", None])

    class _Reader:
        interactive = True

        async def read(self):
            return next(lines)

    app.reader = _Reader()
    app._exit_requested = False
    app._external_refresh_task = None
    app._session_activity = None
    app.goal_controller = SimpleNamespace(close=lambda: None)
    app.render_resume_tail = lambda: None
    app._banner = lambda: None
    app._on_domain_event = lambda _event: None

    assert asyncio.run(app.repl()) == 0
    text = output.getvalue()
    assert text.count("Request failed: Service request timed out") == 2
    assert client.closed


# -- event pump recovery -----------------------------------------------------


def _client_for_pump(renderer) -> tuple[ServiceThreadClient, list]:
    client = object.__new__(ServiceThreadClient)
    sink: list = []
    client._sequence = 0
    client._renderer = renderer
    client._event_sink = sink.append
    client._domain_sink = None
    client.thread = SimpleNamespace(id=THREAD)
    client.turns = SimpleNamespace(
        executing_for_thread=lambda _thread_id: None,
        read=lambda _turn_id: SimpleNamespace(items=()),
    )
    return client, sink


def _view(sequence, type_, payload, *, item_id=None):
    return {
        "id": f"evt_{sequence:026d}",
        "sequence": sequence,
        "type": type_,
        "threadId": THREAD,
        "turnId": TURN,
        "itemId": item_id,
        "payload": payload,
        "timestamp": "2026-10-07T00:00:00+00:00",
    }


def test_unrenderable_event_resyncs_instead_of_stopping_the_pump() -> None:
    class _Fragile(ServiceEventRenderer):
        def convert(self, event):
            if event.type == "turn.plan.updated":
                raise KeyError("plan")
            return super().convert(event)

    client, sink = _client_for_pump(_Fragile())
    page = {
        "events": [
            _view(1, "turn.started", {"turn": {"status": "running"}}),
            _view(2, "turn.plan.updated", {"plan": "not-a-plan"}),
            _view(3, "turn.completed", {"turn": {"status": "completed"}}),
        ]
    }

    client._consume_page(page)

    errors = [e.msg for e in sink if isinstance(e.msg, ErrorEvent)]
    assert len(errors) == 1 and "resynchronized" in errors[0].message
    assert client._sequence == 3


def test_delta_for_an_unknown_item_is_skipped() -> None:
    client, sink = _client_for_pump(ServiceEventRenderer())
    page = {
        "events": [
            _view(1, "turn.started", {"turn": {"status": "running"}}),
            _view(2, "item.delta", {"delta": "late fragment"}, item_id=ITEM),
            _view(3, "turn.completed", {"turn": {"status": "completed"}}),
        ]
    }

    client._consume_page(page)

    assert not any(isinstance(e.msg, AgentMessageDelta) for e in sink)
    assert not any(isinstance(e.msg, ErrorEvent) for e in sink)
    assert any(isinstance(e.msg, TaskComplete) for e in sink)


def test_replay_gap_is_announced_and_later_events_still_render() -> None:
    client, sink = _client_for_pump(ServiceEventRenderer())
    client._sequence = 4
    page = {
        "events": [
            _view(9, "turn.started", {"turn": {"status": "running"}}),
        ]
    }

    client._consume_page(page)

    assert client._sequence == 9
    assert any(
        isinstance(e.msg, ErrorEvent) and "5-8 were missing" in e.msg.message
        for e in sink
    )


def _pushed(client, *values):
    client._inbox = deque(values)
    return client._take_pushes()


def _event_view(sequence, thread=THREAD):
    return {
        **_view(sequence, "turn.started", {}),
        "eventId": f"evt_{sequence}",
        "threadId": thread,
    }


def _live_view(offset, text):
    return {
        "threadId": THREAD,
        "turnId": TURN,
        "itemId": ITEM,
        "channel": "text",
        "offset": offset,
        "nextOffset": offset + len(text.encode()),
        "delta": text,
    }


def test_pushes_keep_their_order_and_skip_other_sessions() -> None:
    # Streamed text must come out between the events it was sent between.
    client, _sink = _client_for_pump(ServiceEventRenderer())
    client._sequence = 4
    delta = _live_view(0, "Hi")
    pushed, replay = _pushed(
        client, _event_view(5), _event_view(9, thread="other"), delta, _event_view(6)
    )
    assert pushed == [_event_view(5), delta, _event_view(6)]
    assert not replay


def test_a_missing_push_or_a_lost_connection_makes_the_pump_replay() -> None:
    client, _sink = _client_for_pump(ServiceEventRenderer())
    client._sequence = 4
    assert _pushed(client, _event_view(5), _event_view(7))[1]
    assert _pushed(client, _event_view(6))[1]
    assert _pushed(client, _REPLAY)[1]


class _Answered(ApplicationError):
    code = "INTERNAL_ERROR"


def test_a_failed_replay_says_lost_connection_only_when_it_was() -> None:
    client, sink = _client_for_pump(ServiceEventRenderer())
    now = time.monotonic()
    # E.g. the database briefly locked: a notice would split the answer.
    assert client._replay_failed(_Answered("database is locked"), 0, now)
    assert not sink
    assert client._replay_failed(_ServiceDown("connection refused"), 0, now)
    assert "Lost connection" in sink[-1].msg.message
    assert not client._replay_failed(_Answered("database is locked"), 3, now - 121)
    assert "keeps failing" in sink[-1].msg.message


class _Unreadable(ApplicationError):
    code = "THREAD_UNREADABLE"


def test_a_session_file_that_cant_be_read_is_not_called_deleted() -> None:
    client, sink = _client_for_pump(ServiceEventRenderer())
    now = time.monotonic()
    unreadable = _Unreadable("Nothing was deleted.")
    assert client._replay_failed(unreadable, 0, now)
    assert "Nothing was deleted" in sink[-1].msg.message
    assert "no longer exists" not in sink[-1].msg.message
    # It keeps trying, past the window that gives up on a lost service.
    assert client._replay_failed(unreadable, 5, now - 600)
    assert len(sink) == 1


# -- failed turn reasons -------------------------------------------------------


def _domain(sequence, type_, payload, *, item_id=None):
    return DomainEvent(
        sequence=sequence,
        type=type_,
        thread_id=THREAD,
        payload=payload,
        turn_id=TURN,
        item_id=item_id,
    )


def test_failed_turn_reason_is_shown_when_no_error_item_reported_it() -> None:
    renderer = ServiceEventRenderer()
    renderer.convert(_domain(1, "turn.started", {"turn": {"status": "running"}}))

    failed = [
        event.msg
        for event in renderer.convert(
            _domain(
                2,
                "turn.failed",
                {
                    "turn": {
                        "status": "failed",
                        "stopReason": "protocol_incomplete",
                        "errorMessage": "stream ended without task_complete",
                    }
                },
            )
        )
    ]

    assert isinstance(failed[0], ErrorEvent)
    assert failed[0].message == "stream ended without task_complete"
    assert isinstance(failed[1], TaskComplete)


def test_failed_turn_reason_is_not_repeated_after_an_error_item() -> None:
    renderer = ServiceEventRenderer()
    renderer.convert(_domain(1, "turn.started", {"turn": {"status": "running"}}))
    renderer.convert(
        _domain(
            2,
            "item.created",
            {
                "item": {
                    "id": ITEM,
                    "kind": "error",
                    "status": "failed",
                    "summary": "provider error",
                    "payload": {"message": "provider error"},
                }
            },
            item_id=ITEM,
        )
    )

    failed = [
        event.msg
        for event in renderer.convert(
            _domain(
                3,
                "turn.failed",
                {"turn": {"status": "failed", "errorMessage": "provider error"}},
            )
        )
    ]

    assert len(failed) == 1 and isinstance(failed[0], TaskComplete)


def test_a_warning_item_reads_as_a_warning_and_never_hides_the_failure() -> None:
    # A failed Turn whose error item could not be saved carries only the
    # warning that some updates were lost; its reason must still be shown.
    renderer = ServiceEventRenderer()
    renderer.convert(_domain(1, "turn.started", {"turn": {"status": "running"}}))
    notice = "Some updates in this turn could not be saved."
    warning = [
        event.msg
        for event in renderer.convert(
            _domain(
                2,
                "item.created",
                {
                    "item": {
                        "id": ITEM,
                        "kind": "error",
                        "status": "completed",
                        "summary": notice,
                        "payload": {
                            "code": "UPDATES_NOT_SAVED",
                            "message": notice,
                            "severity": "warning",
                        },
                    }
                },
                item_id=ITEM,
            )
        )
    ]
    failed = [
        event.msg
        for event in renderer.convert(
            _domain(
                3,
                "turn.failed",
                {"turn": {"status": "failed", "errorMessage": "connection reset"}},
            )
        )
    ]

    assert [type(message) for message in failed] == [ErrorEvent, TaskComplete]
    assert failed[0].message == "connection reset" and not failed[0].warning
    assert [message.warning for message in warning] == [True]

    output = io.StringIO()
    screen = EventRenderer(Console(file=output, color_system=None, width=100))
    for message in (*warning, failed[0]):
        screen.on_event(Event("e", message))
    lines = output.getvalue().splitlines()
    assert f"warning: {notice}" in lines
    assert "error: connection reset" in lines


def test_interrupted_tool_reads_as_interrupted() -> None:
    renderer = ServiceEventRenderer()
    renderer.convert(_domain(1, "turn.started", {"turn": {"status": "running"}}))
    tool = {
        "id": ITEM,
        "kind": "command_execution",
        "status": "in_progress",
        "summary": "pytest",
        "payload": {"callId": "call-1", "name": "bash", "detail": "pytest"},
    }
    renderer.convert(_domain(2, "item.created", {"item": tool}, item_id=ITEM))
    closed = {
        **tool,
        "status": "failed",
        "payload": {**tool["payload"], "interrupted": True},
    }

    completed = [
        event.msg
        for event in renderer.convert(
            _domain(3, "item.updated", {"item": closed}, item_id=ITEM)
        )
    ]

    assert completed[0].result_preview == "Interrupted"
