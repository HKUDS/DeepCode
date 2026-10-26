"""Responses output must survive the next public provider call unchanged."""

import json

import httpx
import pytest
from openai import AsyncOpenAI

from core.providers.openai_compat import OpenAICompatProvider
from core.providers.registry import find_by_name


@pytest.mark.asyncio
@pytest.mark.parametrize("include_item_ids", [True, False])
async def test_responses_tool_result_roundtrip(include_item_ids):
    bodies = []

    def endpoint(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            output = [
                {
                    **({"id": f"fc_{i}"} if include_item_ids else {}),
                    "type": "function_call",
                    "call_id": f"call_{i}",
                    "name": "probe",
                    "arguments": "{}",
                    "status": "completed",
                }
                for i in range(2)
            ]
        else:
            calls = [x for x in body["input"] if x.get("type") == "function_call"]
            results = [
                x for x in body["input"] if x.get("type") == "function_call_output"
            ]
            expected_item_ids = (
                ["fc_0", "fc_1"] if include_item_ids else ["fc_1_0", "fc_1_1"]
            )
            if (
                [x["call_id"] for x in calls] != ["call_0", "call_1"]
                or [x["id"] for x in calls] != expected_item_ids
                or [x["call_id"] for x in results] != ["call_0", "call_1"]
            ):
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "tool identity did not match the preceding response",
                            "type": "invalid_request_error",
                        }
                    },
                )
            output = [
                {
                    "type": "message",
                    "id": "msg_final",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "done", "annotations": []}
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1,
                "model": "gpt-5.4",
                "status": "completed",
                "output": output,
            },
        )

    provider = OpenAICompatProvider(
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        default_model="gpt-5.4",
        protocol="openai_responses",
        spec=find_by_name("openai"),
    )
    await provider._client.close()
    provider._client = AsyncOpenAI(
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint)),
    )
    try:
        history = [{"role": "user", "content": "probe twice"}]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "probe",
                    "description": "Return a probe result",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        first = await provider.chat(history, tools=tools)
        assert len(first.tool_calls) == 2
        history.append(
            {
                "role": "assistant",
                "content": first.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in first.tool_calls
                ],
            }
        )
        history.extend(
            {"role": "tool", "tool_call_id": tc.id, "content": "ok"}
            for tc in first.tool_calls
        )
        final = await provider.chat(history, tools=tools)
        assert final.finish_reason != "error", final.content
        assert final.content == "done"
    finally:
        await provider.aclose()
