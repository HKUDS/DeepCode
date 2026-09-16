"""Bounded, read-only model compatibility probes using the production adapters."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import Any

from core.agent_runtime.helpers import build_assistant_message
from core.domain.execution_profile import ExecutionProfile
from core.providers.base import LLMProvider


def verification_stage(
    stage_id: str,
    status: str,
    detail: str,
    *,
    latency_ms: int | None = None,
    model_count: int | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "status": status,
        "detail": detail[:300],
        "latencyMs": latency_ms,
        "modelCount": model_count,
        "modelId": model_id,
    }


def model_error_detail(response: Any) -> str:
    status = response.error_status_code
    if status == 401:
        return "The provider rejected the API credential"
    if status == 403:
        return "The credential does not have access to this model"
    if status == 404:
        return "The endpoint or selected model was not found"
    if status == 408:
        return "The model verification request timed out"
    if status == 429:
        return "The provider reported a rate, quota, or balance limit"
    if isinstance(status, int) and status >= 500:
        return "The provider is temporarily unavailable"
    if response.error_kind == "timeout":
        return "The model verification request timed out"
    if response.error_kind == "connection":
        return "DeepCode could not connect to the model endpoint"
    return "The provider rejected the model verification request"


async def verify_agent(provider: LLMProvider, profile: ExecutionProfile) -> list[dict]:
    """At most three requests, 90 seconds total, no shell/file/network tools.

    The second request consumes a random value revealed only by the local
    tool result, so merely accepting a tool schema cannot pass the probe.
    """
    stages = []
    stage_ids = ("stream", "tool", "continuation", "reasoning", "image")
    current = "stream"
    started = time.monotonic()
    observed_reasoning = False
    streamed_text = False

    def record(stage, status, detail):
        stages.append(
            verification_stage(
                stage,
                status,
                detail,
                latency_ms=round((time.monotonic() - started) * 1000),
                model_id=profile.model_id,
            )
        )

    async def content_delta(text):
        nonlocal streamed_text
        streamed_text = streamed_text or bool(text)

    async def reasoning_delta(text, _channel):
        nonlocal observed_reasoning
        observed_reasoning = observed_reasoning or bool(text)

    async def request(messages, **kwargs):
        response = await provider.chat_stream(
            messages=messages,
            model=profile.model_id,
            max_tokens=min(1024, profile.max_output_tokens),
            temperature=0,
            reasoning_effort=profile.reasoning_effort,
            on_content_delta=content_delta,
            on_reasoning_delta=reasoning_delta,
            **kwargs,
        )
        if response.finish_reason == "error":
            raise ValueError(model_error_detail(response))
        return response

    try:
        async with asyncio.timeout(90):
            if profile.tool_calling is False:
                record(
                    "tool",
                    "skipped",
                    "Model declared without tool calling; Agent compatibility cannot be established",
                )
                record("continuation", "skipped", "Tool calling is disabled")
                reply = await request([{"role": "user", "content": "Reply with OK"}])
                record(
                    "stream",
                    "passed" if streamed_text and reply.content else "failed",
                    "Text stream checked",
                )
            else:
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": "deepcode_probe",
                            "description": "Return a verification nonce. Call once with value 7.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "value": {"type": "integer", "enum": [7]}
                                },
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        },
                    }
                ]
                messages = [
                    {
                        "role": "user",
                        "content": "Call deepcode_probe once with value 7, then reply with exactly the nonce returned by the tool.",
                    }
                ]
                first = await request(
                    messages,
                    tools=tools,
                    tool_choice="auto",
                )
                current = "tool"
                if not first.should_execute_tools or len(first.tool_calls) != 1:
                    raise ValueError("Expected one completed tool call")
                call = first.tool_calls[0]
                if (
                    call.name != "deepcode_probe"
                    or call.arguments != {"value": 7}
                    or not call.id
                ):
                    raise ValueError("The tool call name, ID or arguments were invalid")
                record(
                    "tool",
                    "passed",
                    "One valid tool call executed by the local verification function",
                )
                nonce = secrets.token_hex(12)
                messages += [
                    build_assistant_message(
                        first.content,
                        tool_calls=[call.to_openai_tool_call()],
                        reasoning_content=first.reasoning_content,
                        reasoning_summary=first.reasoning_summary,
                        provider_state=first.provider_state,
                        thinking_blocks=first.thinking_blocks,
                    ),
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps({"nonce": nonce}),
                    },
                ]
                current = "continuation"
                second = await request(messages, tools=tools, tool_choice="auto")
                if second.tool_calls or (second.content or "").strip() != nonce:
                    raise ValueError(
                        "The model did not consume and reproduce the tool result"
                    )
                record(
                    "continuation",
                    "passed",
                    "The model consumed the tool result with production reasoning-history serialization",
                )
                record(
                    "stream",
                    "passed" if streamed_text else "failed",
                    "Text deltas received"
                    if streamed_text
                    else "No text deltas received",
                )
            record(
                "reasoning",
                "passed" if observed_reasoning else "skipped",
                "Provider reasoning deltas observed and history accepted"
                if observed_reasoning
                else "No reasoning deltas observed; reasoning remains unverified",
            )
            current = "image"
            if (
                profile.input_modalities is None
                or "image" not in profile.input_modalities
            ):
                record("image", "skipped", "Image input is not explicitly declared")
            else:
                # A valid 64x64 PNG; this checks protocol acceptance, not vision quality.
                png = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAXklEQVR4nO3PMQ0AMAzAsPInvYLYYVWKESTzjhsd8KsBrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BbQHKU9LC7/CP1AAAAABJRU5ErkJggg=="
                image = await request(
                    [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "An image is attached. Reply OK.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64," + png
                                    },
                                },
                            ],
                        }
                    ]
                )
                record(
                    "image",
                    "passed" if image.content else "failed",
                    "Image request accepted; visual understanding is not evaluated",
                )
    except TimeoutError:
        record(
            current,
            "failed",
            "Compatibility verification exceeded its 90 second budget",
        )
    except ValueError as exc:
        record(current, "failed", str(exc))
    except Exception:
        record(current, "failed", "Compatibility verification could not be completed")
    finally:
        await provider.aclose()
    recorded = {stage["id"] for stage in stages}
    for stage in stage_ids:
        if stage not in recorded:
            record(stage, "not_run", "An earlier stage did not complete")
    return sorted(stages, key=lambda stage: stage_ids.index(stage["id"]))
