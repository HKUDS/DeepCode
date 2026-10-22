"""Missing Responses item IDs must not collide across parallel tool calls."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from core.providers.openai_responses.converters import convert_messages
from core.providers.openai_responses.parsing import (
    consume_sdk_stream,
    consume_sse,
    parse_response_output,
)


def _assert_converter_assigns_unique_item_ids(tool_calls) -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in tool_calls
            ],
        }
    ]
    _, items = convert_messages(messages)
    calls = [item for item in items if item.get("type") == "function_call"]
    assert [item["call_id"] for item in calls] == ["call_0", "call_1"]
    assert [item["id"] for item in calls] == ["fc_0_0", "fc_0_1"]


def test_sdk_response_omitted_item_ids_defer_to_converter_fallback() -> None:
    response = parse_response_output(
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": f"call_{index}",
                    "name": "probe",
                    "arguments": "{}",
                }
                for index in range(2)
            ],
        }
    )

    assert [call.id for call in response.tool_calls] == ["call_0", "call_1"]
    _assert_converter_assigns_unique_item_ids(response.tool_calls)


@pytest.mark.asyncio
async def test_sse_omitted_item_ids_defer_to_converter_fallback() -> None:
    events = []
    for index in range(2):
        item = {
            "type": "function_call",
            "call_id": f"call_{index}",
            "name": "probe",
            "arguments": "{}",
        }
        events.extend(
            [
                {"type": "response.output_item.added", "item": item},
                {"type": "response.output_item.done", "item": item},
            ]
        )
    events.append({"type": "response.completed", "response": {"status": "completed"}})
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

    _content, tool_calls, finish_reason = await consume_sse(
        httpx.Response(200, content=payload.encode("utf-8"))
    )

    assert finish_reason == "stop"
    assert [call.id for call in tool_calls] == ["call_0", "call_1"]
    _assert_converter_assigns_unique_item_ids(tool_calls)


@pytest.mark.asyncio
async def test_sdk_stream_omitted_item_ids_defer_to_converter_fallback() -> None:
    events = []
    for index in range(2):
        item = SimpleNamespace(
            type="function_call",
            call_id=f"call_{index}",
            name="probe",
            arguments="{}",
        )
        events.extend(
            [
                SimpleNamespace(type="response.output_item.added", item=item),
                SimpleNamespace(type="response.output_item.done", item=item),
            ]
        )
    events.append(
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(status="completed", usage=None, output=[]),
        )
    )

    async def stream():
        for event in events:
            yield event

    (
        _content,
        tool_calls,
        finish_reason,
        _usage,
        _reasoning,
        _state,
    ) = await consume_sdk_stream(stream())

    assert finish_reason == "stop"
    assert [call.id for call in tool_calls] == ["call_0", "call_1"]
    _assert_converter_assigns_unique_item_ids(tool_calls)
