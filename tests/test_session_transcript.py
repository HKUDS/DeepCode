"""Canonical history records reconstruct the model-visible list."""

from __future__ import annotations

from core.sessions.models import SessionMessage
from core.sessions.transcript import (
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
