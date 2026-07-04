"""Tests for the L1 structured parts message model + converter."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.events.parts import (  # noqa: E402
    Message,
    ReasoningPart,
    TextPart,
    ToolPart,
    ToolState,
    messages_to_parts,
    serialize_message,
)


def test_system_and_user_become_text_messages():
    parts = messages_to_parts(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert parts == [
        Message(role="system", parts=[TextPart(text="sys")]),
        Message(role="user", parts=[TextPart(text="hi")]),
    ]


def test_assistant_tool_call_paired_with_result_completed():
    msgs = [
        {
            "role": "assistant",
            "content": "writing",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"file_path": "a.py"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "write_file", "content": "ok"},
    ]
    parts = messages_to_parts(msgs)
    assert len(parts) == 1  # the tool result is absorbed
    assistant = parts[0]
    assert isinstance(assistant.parts[0], TextPart)
    tool = assistant.parts[1]
    assert isinstance(tool, ToolPart)
    assert tool.state is ToolState.COMPLETED
    assert tool.arguments == {"file_path": "a.py"}
    assert tool.result == "ok"


def test_tool_result_error_sets_error_state():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "Error: permission denied: blocked",
        },
    ]
    tool = messages_to_parts(msgs)[0].parts[0]
    assert isinstance(tool, ToolPart)
    assert tool.state is ToolState.ERROR
    assert "permission denied" in (tool.error or "")


def test_unfulfilled_tool_call_is_pending():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c9",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        }
    ]
    tool = messages_to_parts(msgs)[0].parts[0]
    assert tool.state is ToolState.PENDING
    assert tool.result is None


def test_reasoning_part_precedes_text():
    msgs = [{"role": "assistant", "content": "answer", "reasoning_content": "think"}]
    parts = messages_to_parts(msgs)[0].parts
    assert isinstance(parts[0], ReasoningPart)
    assert isinstance(parts[1], TextPart)


def test_malformed_tool_arguments_preserved_raw():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{bad"},
                }
            ],
        }
    ]
    tool = messages_to_parts(msgs)[0].parts[0]
    assert tool.arguments == {"__raw__": "{bad"}


def test_serialize_message_round_trips_state_to_value():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "w", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    ser = serialize_message(messages_to_parts(msgs)[0])
    assert ser["role"] == "assistant"
    tool = [p for p in ser["parts"] if p["type"] == "tool"][0]
    assert tool["state"] == "completed"  # enum serialized to its value
