"""Canonical history records reconstruct the model-visible list."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_runtime.tools.registry import ToolRegistry
from core.events import AgentSession
from core.events.protocol import UserInput
from core.providers.base import LLMResponse, ToolCallRequest
from core.sessions.models import SessionMessage
from core.sessions.transcript import (
    is_context_note,
    new_records_from_history,
    visible_kernel_history,
)


def test_tool_bearing_history_round_trips() -> None:
    history = [
        {"role": "user", "content": "run echo"},
        {
            "role": "assistant",
            "content": "calling",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"echo hi"}'},
                }
            ],
        },
        {
            "role": "tool",
            "content": "SECRET-42",
            "tool_call_id": "c1",
            "name": "bash",
        },
        {"role": "assistant", "content": "done"},
    ]
    stored = new_records_from_history(history, stored=())
    rebuilt = visible_kernel_history(stored)
    assert rebuilt[1]["tool_calls"][0]["id"] == "c1"
    assert rebuilt[2]["content"] == "SECRET-42"
    assert rebuilt[2]["tool_call_id"] == "c1"
    assert rebuilt[-1]["content"] == "done"


def test_compaction_reset_rebuilds_checkpoint_plus_tail() -> None:
    stored = [
        SessionMessage(role="user", content="old"),
        SessionMessage(role="assistant", content="old-a"),
        SessionMessage(role="user", content="keep-me"),
        SessionMessage(role="assistant", content="keep-a"),
        SessionMessage(
            role="user",
            content="An earlier agent worked on this task and produced the summary",
            metadata={"compaction": {"reset": True, "retain": 2}},
        ),
        SessionMessage(role="user", content="new question"),
    ]
    rebuilt = visible_kernel_history(stored)
    contents = [item["content"] for item in rebuilt]
    assert "old" not in contents
    assert "keep-me" in contents
    assert "keep-a" in contents
    assert contents[0].startswith("An earlier agent")
    assert contents[-1] == "new question"


def test_new_records_skip_already_stored() -> None:
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    first = new_records_from_history(history, stored=())
    second = new_records_from_history(history, stored=first)
    assert [item.content for item in first] == ["hi", "yo"]
    assert second == []


def test_a_repeated_message_is_a_new_message() -> None:
    stored = [
        SessionMessage(role="user", content="yes"),
        SessionMessage(role="assistant", content="ok"),
        SessionMessage(role="user", content="yes"),
    ]
    history = [
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "ok"},
        {"role": "assistant", "content": "ok"},
    ]
    added = new_records_from_history(history, stored)
    assert [(item.role, item.content) for item in added] == [
        ("assistant", "ok"),
        ("assistant", "ok"),
    ]


def _checkpoint(summary: str, retain: int) -> SessionMessage:
    return SessionMessage(
        role="user",
        content=summary,
        metadata={"compaction": {"reset": True, "retain": retain}},
    )


def test_a_reply_repeated_after_a_compaction_is_saved() -> None:
    stored = [
        SessionMessage(role="user", content="q1"),
        SessionMessage(role="assistant", content="Done."),
        _checkpoint("summary of q1", retain=0),
        SessionMessage(role="user", content="q2"),
    ]
    # The resumed history no longer holds the first "Done.".
    history = [
        *visible_kernel_history(stored),
        {"role": "assistant", "content": "Done."},
    ]

    added = new_records_from_history(history, stored)

    assert [(item.role, item.content) for item in added] == [("assistant", "Done.")]


def test_what_a_compaction_kept_is_resumed_and_never_saved_twice() -> None:
    # Saved while the Turn ran: its prompt, then a steer and a note, which
    # reach the file before the Turn's own replies do.
    stored = [
        SessionMessage(role="user", content="u_prev"),
        SessionMessage(role="assistant", content="a_prev"),
        SessionMessage(role="user", content="u_turn"),
        SessionMessage(role="user", content="steer"),
        SessionMessage(role="user", content="note"),
    ]
    # Compacted mid-turn: the checkpoint replaced u_prev, the tail was kept,
    # and the steer and the note joined the history where the model read them.
    history = [
        {
            "role": "user",
            "content": "summary of u_prev",
            "compaction": {"reset": True, "retain": 0},
        },
        {"role": "assistant", "content": "a_prev"},
        {"role": "user", "content": "u_turn"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "steer"},
        {"role": "user", "content": "note"},
        {"role": "assistant", "content": "a_final"},
    ]
    stored += new_records_from_history(history, stored)

    resumed = [entry["content"] for entry in visible_kernel_history(stored)]
    assert resumed[0] == "summary of u_prev"
    assert {"a_prev", "u_turn", "steer", "note", "a1", "a_final"} <= set(resumed)
    assert "u_prev" not in resumed

    # The next Turn, on the same resident history: only its own reply is new.
    stored.append(SessionMessage(role="user", content="u_next"))
    history += [
        {"role": "user", "content": "u_next"},
        {"role": "assistant", "content": "a_next"},
    ]
    added = new_records_from_history(history, stored)
    assert [(item.role, item.content) for item in added] == [("assistant", "a_next")]


def test_a_tool_result_pruned_under_pressure_is_not_saved_again() -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "read"}}
    stored = [
        SessionMessage(role="user", content="read it"),
        SessionMessage(role="assistant", content="", metadata={"toolCalls": [call]}),
        SessionMessage(
            role="tool",
            content="x" * 10_000,
            metadata={"toolCallId": "c1", "name": "read"},
        ),
        SessionMessage(role="assistant", content="read"),
        SessionMessage(role="user", content="again"),
    ]
    history = visible_kernel_history(stored)
    # The pruner cut the middle out of the old result, in the history itself.
    history[2] = {**history[2], "content": "x" * 10 + "[... pruned ...]" + "x" * 10}
    history.append({"role": "assistant", "content": "done"})

    added = new_records_from_history(history, stored)

    assert [(item.role, item.content) for item in added] == [("assistant", "done")]


def test_a_nameless_call_is_dropped_before_it_is_stored() -> None:
    # A parser glitch can hand back a call whose function carries the raw
    # payload but no name. The API rejects a request that replays one
    # (400/422 missing field name), so it must never reach the store.
    history = [
        {"role": "user", "content": "run echo"},
        {
            "role": "assistant",
            "content": "calling",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"arguments": '{"command":"echo hi"}'},
                },
                {"id": "c2", "type": "function", "function": {"name": "   "}},
                {
                    "id": "c3",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"echo hi"}'},
                },
            ],
        },
        {"role": "tool", "content": "hi", "tool_call_id": "c3", "name": "bash"},
    ]
    stored = new_records_from_history(history, stored=())
    calls = (stored[1].metadata or {})["toolCalls"]
    assert [call["id"] for call in calls] == ["c3"]


def test_a_stored_nameless_call_is_dropped_on_resume() -> None:
    # A record poisoned before this guard existed self-heals on resume:
    # its text stays, the undeliverable call goes.
    stored = [
        SessionMessage(role="user", content="run echo"),
        SessionMessage(
            role="assistant",
            content="calling",
            metadata={
                "toolCalls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"arguments": '{"command":"echo hi"}'},
                    }
                ]
            },
        ),
    ]
    rebuilt = visible_kernel_history(stored)
    assert rebuilt[1]["content"] == "calling"
    assert "tool_calls" not in rebuilt[1]


def test_a_turn_of_only_nameless_calls_leaves_nothing_behind() -> None:
    # No text and no deliverable call means there is no message to keep.
    history = [
        {"role": "user", "content": "run echo"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {}}],
        },
    ]
    stored = new_records_from_history(history, stored=())
    assert [item.role for item in stored] == ["user"]
    assert [item["role"] for item in visible_kernel_history(stored)] == ["user"]


def test_a_resumed_poisoned_turn_is_not_stored_again() -> None:
    # The fingerprint must see the same surviving calls the replay does,
    # or every resume re-saves the cleaned turn as a second message.
    stored = [
        SessionMessage(role="user", content="run echo"),
        SessionMessage(
            role="assistant",
            content="calling",
            metadata={
                "toolCalls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"arguments": "{}"},
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ]
            },
        ),
    ]
    history = [
        *visible_kernel_history(stored),
        {"role": "assistant", "content": "done"},
    ]
    added = new_records_from_history(history, stored)
    assert [(item.role, item.content) for item in added] == [("assistant", "done")]


def test_runtime_written_user_records_come_back_marked() -> None:
    """A resumed history marks what the runtime wrote, as the live one does."""
    from core.agent_runtime.helpers import CONTEXT_NOTE_KEY

    stored = [
        SessionMessage("user", "build the parser", metadata={"source": "start"}),
        SessionMessage(
            "user",
            "Continue the Goal.",
            metadata={"source": "goal_continuation", "client": "internal"},
        ),
        SessionMessage(
            "user",
            "you repeated yourself",
            metadata={"delivery": "mid_turn", "source": "repeat_guard"},
        ),
        SessionMessage(
            "user", "also add --strict", metadata={"source": "steer", "messageId": "m1"}
        ),
        SessionMessage(
            "user",
            "[The previous Turn was interrupted before it completed.]",
            metadata={"source": "turn_interrupt", "client": "internal"},
        ),
    ]
    rebuilt = visible_kernel_history(stored)
    assert [entry.get(CONTEXT_NOTE_KEY) for entry in rebuilt] == [
        None,
        "goal_continuation",
        "repeat_guard",
        None,
        "turn_interrupt",
    ]


def test_only_runtime_notes_count_as_context_notes() -> None:
    def user(**metadata: str) -> SessionMessage:
        return SessionMessage("user", "text", metadata=metadata)

    assert is_context_note(user(delivery="mid_turn", source="goal_update"))
    assert is_context_note(user(delivery="between_turns", source="subagent"))
    assert is_context_note(user(source="turn_interrupt"))
    # What a person sent, whenever it was delivered, is not a note.
    assert not is_context_note(user(delivery="current_turn", source="start"))
    assert not is_context_note(user(source="steer", messageId="m1"))
    assert not is_context_note(
        SessionMessage("assistant", "text", metadata={"delivery": "mid_turn"})
    )


class _NamelessCallProvider:
    """Records every request and replays a scripted response list."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append(list(kwargs.get("messages") or []))
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


def _nameless_calls(messages: list[dict[str, Any]]) -> list[str]:
    """Ids of the tool calls in ``messages`` that name no function."""
    found: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or ():
            function = call.get("function") if isinstance(call, dict) else None
            name = (function or {}).get("name")
            if not isinstance(name, str) or not name.strip():
                found.append(str(call.get("id")))
    return found


@pytest.mark.asyncio
async def test_a_live_session_never_sends_a_nameless_tool_call() -> None:
    """Storage is healed by the record boundary; a live run must heal too.

    ``AgentSession._history`` is kept in memory between turns, so dropping the
    nameless call on save and on rebuild is not enough: the follow-up request in
    the same turn, and every later turn, would still carry it until a restart.
    """
    provider = _NamelessCallProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="nameless-1", name="", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="after the tool turn", finish_reason="stop"),
            LLMResponse(content="second turn", finish_reason="stop"),
        ]
    )
    session = AgentSession(provider, ToolRegistry(), model="fake-model")

    await session.submit(UserInput(text="first"))
    first_events = session.drain_events()
    await session.submit(UserInput(text="second"))
    second_events = session.drain_events()

    # The follow-up request of the same turn, and every later request, is clean.
    assert len(provider.requests) >= 3
    for index, request in enumerate(provider.requests[1:], start=1):
        assert _nameless_calls(request) == [], (
            f"request {index} carried a nameless tool call"
        )

    # The poisoned turn ends, and the turn after it succeeds.
    assert [event.msg.type for event in first_events][-1] == "task_complete"
    assert [event.msg.type for event in second_events][-1] == "task_complete"
