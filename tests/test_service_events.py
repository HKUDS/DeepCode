"""Service event conversion and rendering for the shared-service TUI path."""

from __future__ import annotations

import io

from rich.console import Console

from cli.service_events import ServiceEventRenderer
from cli.tui.renderer import EventRenderer
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.events import (
    AgentMessage,
    AgentMessageDelta,
    Event,
    TaskComplete,
    TurnStarted,
)

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


def _live(offset, text, *, channel="text", item_id=ITEM):
    return {
        "threadId": THREAD,
        "turnId": TURN_A,
        "itemId": item_id,
        "channel": channel,
        "offset": offset,
        "nextOffset": offset + len(text.encode()),
        "delta": text,
    }


def _followed(renderer, text="你好"):
    """A renderer that has shown the assistant item's first text."""

    _messages(
        renderer,
        [
            _event(1, "turn.started", {"turn": {"status": "running"}}),
            _event(
                2,
                "item.created",
                _assistant("in_progress", text, streaming=True),
                item_id=ITEM,
            ),
        ],
    )


def _deltas(rendered):
    return [event.msg.delta for event in rendered]


def test_live_text_is_rendered_once_from_its_byte_offset() -> None:
    renderer = ServiceEventRenderer()
    _followed(renderer)
    shown = len("你好".encode())

    assert _deltas(renderer.convert_live(_live(shown, "😀 world"))) == ["😀 world"]
    # Re-sent from an earlier offset: only the unseen part, at a whole character.
    assert _deltas(renderer.convert_live(_live(shown + 4, " world!"))) == ["!"]
    assert renderer.convert_live(_live(shown, "😀")) == []
    # A missed delta: the caller reads the text so far.
    assert renderer.convert_live(_live(100, "later")) is None
    # Not followed (it began before this client attached): its completion
    # carries the text.
    assert renderer.convert_live(_live(0, "x", item_id="item_other")) == []


def test_a_read_after_a_missed_delta_shows_only_what_is_missing() -> None:
    renderer = ServiceEventRenderer()
    _followed(renderer)
    renderer.convert_live(_live(len("你好".encode()), "，世界"))
    read = Item(
        id=ITEM,
        thread_id=THREAD,
        turn_id=TURN_A,
        ordinal=1,
        kind=ItemKind.ASSISTANT_MESSAGE,
        status=ItemStatus.IN_PROGRESS,
        summary="",
        payload={"text": "你好，世界 and more", "messageId": "msg-1"},
    )

    assert _deltas(renderer.convert_streamed([read])) == [" and more"]
    assert renderer.convert_streamed([read]) == []
    following = len("你好，世界 and more".encode())
    assert _deltas(renderer.convert_live(_live(following, "."))) == ["."]


def test_live_reasoning_streams_each_channel() -> None:
    renderer = ServiceEventRenderer()
    _messages(
        renderer,
        [
            _event(1, "turn.started", {"turn": {"status": "running"}}),
            _event(
                2,
                "item.created",
                {
                    "item": {
                        "id": ITEM,
                        "kind": "reasoning_summary",
                        "status": "in_progress",
                        "summary": "Thinking",
                        "payload": {
                            "summaryText": "",
                            "traceText": "",
                            "streaming": True,
                        },
                    }
                },
                item_id=ITEM,
            ),
        ],
    )
    summary = renderer.convert_live(_live(0, "plan", channel="summary"))
    trace = renderer.convert_live(_live(0, "trace", channel="provider_trace"))

    assert [(m.msg.channel.value, m.msg.delta) for m in summary + trace] == [
        ("summary", "plan"),
        ("provider_trace", "trace"),
    ]


def test_a_refused_tool_call_stays_refused_through_the_service() -> None:
    refused, broken = "item_" + "d" * 26, "item_" + "e" * 26

    def tool(status: str, *, item_id: str) -> dict:
        return {
            "item": {
                "id": item_id,
                "kind": "command_execution",
                "status": status,
                "summary": "rm -rf build",
                "payload": {
                    "callId": f"call-{item_id}",
                    "name": "bash",
                    "isError": status != "completed",
                    "resultPreview": "Error: permission denied: blocked",
                },
            }
        }

    converter = ServiceEventRenderer()
    completed = _messages(
        converter,
        [
            _event(1, "turn.started", {"turn": {"status": "running"}}),
            _event(
                2, "item.updated", tool("declined", item_id=refused), item_id=refused
            ),
            _event(3, "item.updated", tool("failed", item_id=broken), item_id=broken),
        ],
    )

    assert [
        (done.call_id, done.denied)
        for done in completed
        if done.type == "tool_completed"
    ] == [
        (f"call-{refused}", True),
        (f"call-{broken}", False),
    ]


def _running_item(item_id, kind, payload, *, ordinal=1):
    return Item(
        id=item_id,
        thread_id=THREAD,
        turn_id=TURN_A,
        ordinal=ordinal,
        kind=kind,
        status=ItemStatus.IN_PROGRESS,
        summary="",
        payload=payload,
    )


def test_joining_a_running_turn_shows_what_it_said_so_far_once() -> None:
    """A client that attaches mid-answer, as `--resume` into a running Turn.

    Its earlier text used to be skipped, so the live text started mid-answer
    and the completion then printed the whole answer again.
    """
    renderer = ServiceEventRenderer()
    so_far = "First line.\nSecond li"
    answer = _running_item(
        ITEM, ItemKind.ASSISTANT_MESSAGE, {"text": so_far, "messageId": "msg-1"}
    )

    joined = renderer.join(TURN_A, [answer])
    live = renderer.convert_live(_live(len(so_far.encode()), "ne.\nThird line."))
    done = _messages(
        renderer,
        [
            _event(
                5,
                "item.updated",
                _assistant(
                    "completed",
                    "First line.\nSecond line.\nThird line.",
                    streaming=False,
                ),
                item_id=ITEM,
            ),
            _event(6, "turn.completed", {"turn": {"status": "completed"}}),
        ],
    )

    assert isinstance(joined[0].msg, TurnStarted)
    text = _render([event.msg for event in joined + live] + done)
    for line in ("First line.", "Second line.", "Third line."):
        assert text.count(line) == 1, text
    assert text.index("First") < text.index("Second") < text.index("Third")


def test_joining_shows_the_thinking_and_the_tool_in_progress() -> None:
    renderer = ServiceEventRenderer()
    thinking = _running_item(
        ITEM,
        ItemKind.REASONING_SUMMARY,
        {"summaryText": "Weighing it", "traceText": "", "streaming": True},
        ordinal=2,
    )
    tool = _running_item(
        "item_" + "t" * 26,
        ItemKind.TOOL_CALL,
        {"callId": "call-1", "name": "bash", "detail": "pytest -q"},
        ordinal=3,
    )
    finished = Item(
        thread_id=THREAD,
        turn_id=TURN_A,
        ordinal=1,
        kind=ItemKind.TOOL_CALL,
        status=ItemStatus.COMPLETED,
        summary="done",
        payload={"callId": "call-0", "name": "read"},
    )

    shown = [event.msg for event in renderer.join(TURN_A, [finished, thinking, tool])]

    assert [type(message).__name__ for message in shown] == [
        "TurnStarted",
        "AgentReasoningStarted",
        "AgentReasoningDelta",
        "ToolStarted",
    ]
    assert shown[3].call_id == "call-1"
    # The thinking continues from what was shown.
    more = renderer.convert_live(_live(len(b"Weighing it"), " up", channel="summary"))
    assert [event.msg.delta for event in more] == [" up"]
