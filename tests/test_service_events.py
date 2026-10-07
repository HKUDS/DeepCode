"""Service event conversion and rendering for the shared-service TUI path."""

from __future__ import annotations

import io

from rich.console import Console

from cli.service_events import ServiceEventRenderer
from cli.tui.renderer import EventRenderer
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.events import AgentMessage, AgentMessageDelta, Event, TaskComplete

THREAD = "thread-1"
TURN_A = "turn_" + "a" * 26
TURN_B = "turn_" + "b" * 26
ITEM = "item_" + "c" * 26


def _event(sequence, type_, payload, *, turn_id=TURN_A, item_id=None):
    return DomainEvent(
        sequence=sequence,
        type=type_,
        thread_id=THREAD,
        payload=payload,
        turn_id=turn_id,
        item_id=item_id,
    )


def _assistant(status: str, text: str, *, streaming: bool) -> dict:
    return {
        "item": {
            "id": ITEM,
            "kind": "assistant_message",
            "status": status,
            "summary": text[:160],
            "payload": {
                "text": text,
                "streaming": streaming,
                "messageId": "msg-1",
                "phase": "final_answer" if not streaming else "unknown",
            },
        }
    }


def _messages(renderer, events):
    return [message.msg for event in events for message in renderer.convert(event)]


def test_another_turns_completion_does_not_end_the_streaming_turn() -> None:
    renderer = ServiceEventRenderer()
    started = _messages(
        renderer,
        [_event(1, "turn.started", {"turn": {"status": "running"}})],
    )

    other = _messages(
        renderer,
        [
            _event(
                2,
                "turn.interrupted",
                {"turn": {"status": "interrupted"}},
                turn_id=TURN_B,
            )
        ],
    )
    own = _messages(
        renderer,
        [_event(3, "turn.completed", {"turn": {"status": "completed"}})],
    )

    assert len(started) == 1
    assert other == []
    assert len(own) == 1 and isinstance(own[0], TaskComplete)


def test_seed_follows_the_running_turn_and_skips_finished_items() -> None:
    renderer = ServiceEventRenderer()
    running = Item(
        thread_id=THREAD,
        turn_id=TURN_A,
        ordinal=2,
        kind=ItemKind.ASSISTANT_MESSAGE,
        status=ItemStatus.IN_PROGRESS,
        summary="Hel",
        payload={"text": "Hel", "messageId": "msg-1"},
        id=ITEM,
    )
    finished = [
        Item(
            thread_id=THREAD,
            turn_id=TURN_A,
            ordinal=ordinal,
            kind=ItemKind.TOOL_CALL,
            status=ItemStatus.COMPLETED,
            summary="done",
            payload={"callId": f"c{ordinal}"},
        )
        for ordinal in range(3, 3000)
    ]

    renderer.seed([running, *finished], turn_id=TURN_A)
    delta = _messages(
        renderer,
        [_event(5, "item.delta", {"delta": "lo"}, item_id=ITEM)],
    )
    done = _messages(
        renderer,
        [_event(6, "turn.completed", {"turn": {"status": "completed"}})],
    )

    assert [(m.delta, m.message_id) for m in delta] == [("lo", "msg-1")]
    assert len(done) == 1 and isinstance(done[0], TaskComplete)


def _render(messages) -> str:
    output = io.StringIO()
    renderer = EventRenderer(Console(file=output, color_system=None, width=120))
    for index, message in enumerate(messages):
        renderer.on_event(Event(str(index), message))
    return output.getvalue()


def test_final_text_extending_the_stream_prints_only_the_missing_suffix() -> None:
    text = _render(
        [
            AgentMessageDelta("我是 DeepCode。如果你需要了解模型版本或", "msg-1"),
            AgentMessage(
                "我是 DeepCode。如果你需要了解模型版本或技术细节，请查阅文档。",
                "msg-1",
            ),
        ]
    )

    assert text.count("我是 DeepCode") == 1
    assert "技术细节，请查阅文档。" in text


def test_identical_final_text_is_not_reprinted() -> None:
    text = _render(
        [
            AgentMessageDelta("first line\nsecond", "msg-1"),
            AgentMessage("first line\nsecond", "msg-1"),
        ]
    )

    assert text.count("first line") == 1
    assert text.count("second") == 1


def test_divergent_final_text_is_still_shown_in_full() -> None:
    text = _render(
        [
            AgentMessageDelta("draft answer", "msg-1"),
            AgentMessage("Corrected answer", "msg-1"),
        ]
    )

    assert "Corrected answer" in text


def test_service_stream_completion_renders_each_line_once() -> None:
    """End to end through conversion: created + delta + completed item."""

    converter = ServiceEventRenderer()
    events = [
        _event(1, "turn.started", {"turn": {"status": "running"}}),
        _event(
            2,
            "item.created",
            _assistant("in_progress", "Hello ", streaming=True),
            item_id=ITEM,
        ),
        _event(3, "item.delta", {"delta": "world"}, item_id=ITEM),
        _event(
            4,
            "item.updated",
            _assistant("completed", "Hello world, done.", streaming=False),
            item_id=ITEM,
        ),
        _event(5, "turn.completed", {"turn": {"status": "completed"}}),
    ]

    text = _render(_messages(converter, events))

    assert text.count("Hello world") == 1
    assert ", done." in text
