"""Tests for the L0 provider-stream event vocabulary."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.events.llm_events import (  # noqa: E402
    ReasoningDelta,
    StreamError,
    TextDelta,
    ToolCallEnd,
    UsageInfo,
    llm_response_to_events,
    serialize_llm_event,
)
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402


def test_text_only_response():
    events = llm_response_to_events(
        LLMResponse(content="hello", finish_reason="stop", usage={})
    )
    assert events == [TextDelta(text="hello")]


def test_reasoning_precedes_text():
    events = llm_response_to_events(
        LLMResponse(
            content="answer",
            reasoning_content="thinking...",
            finish_reason="stop",
        )
    )
    assert isinstance(events[0], ReasoningDelta)
    assert isinstance(events[1], TextDelta)


def test_tool_calls_become_tool_call_end():
    resp = LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest(id="c1", name="write_file", arguments={"file_path": "a.py"})
        ],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    events = llm_response_to_events(resp)
    tool_ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert tool_ends == [
        ToolCallEnd(id="c1", name="write_file", arguments={"file_path": "a.py"})
    ]
    usage = [e for e in events if isinstance(e, UsageInfo)][0]
    assert usage.total_tokens == 15  # derived when not provided


def test_error_response_is_single_stream_error():
    events = llm_response_to_events(
        LLMResponse(content="boom", finish_reason="error", error_kind="timeout")
    )
    assert events == [StreamError(message="boom", kind="timeout")]


def test_serialize_includes_type_discriminator():
    assert serialize_llm_event(TextDelta(text="x")) == {
        "text": "x",
        "type": "text_delta",
    }
    assert serialize_llm_event(ToolCallEnd(id="c1", name="t", arguments={"a": 1})) == {
        "id": "c1",
        "name": "t",
        "arguments": {"a": 1},
        "type": "tool_call_end",
    }
    assert serialize_llm_event(StreamError(message="e"))["type"] == "error"


def test_type_field_is_stable_and_not_constructor_arg():
    # ``type`` is a fixed discriminator, not something callers pass in.
    ev = TextDelta(text="hi")
    assert ev.type == "text_delta"
