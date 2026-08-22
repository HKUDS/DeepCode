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
