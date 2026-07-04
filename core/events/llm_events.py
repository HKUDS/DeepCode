"""The provider-stream event vocabulary (L0).

Every LLM backend — the current ``LLMResponse`` shape, a future streaming
adapter, Anthropic, OpenAI-compatible — normalizes its output to this one
event union. Consumers (the agent loop, a UI, a logger, the SQ/EQ bridge)
only ever see these events, so provider quirks never leak past the boundary.

The events mirror the natural stream order:

    (text_delta | reasoning_delta | tool_call_start → tool_call_delta* →
     tool_call_end)*  →  usage?  →  (error?)

A *completed* ``LLMResponse`` (the non-streaming path DeepCode uses today)
converts to the terminal forms via :func:`llm_response_to_events`: whole
text as one ``TextDelta``, each tool call as a single ``ToolCallEnd``, then
``UsageInfo`` — i.e. the same vocabulary, just not chunked. This keeps one
representation whether or not the provider streams.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Union

from core.providers.base import LLMResponse


@dataclass(frozen=True)
class TextDelta:
    """A chunk of assistant visible text."""

    text: str
    type: str = field(default="text_delta", init=False)


@dataclass(frozen=True)
class ReasoningDelta:
    """A chunk of reasoning / thinking content (not user-visible answer)."""

    text: str
    type: str = field(default="reasoning_delta", init=False)


@dataclass(frozen=True)
class ToolCallStart:
    """A tool call has begun streaming (id + name known, args pending)."""

    id: str
    name: str
    type: str = field(default="tool_call_start", init=False)


@dataclass(frozen=True)
class ToolCallDelta:
    """A chunk of a tool call's streamed JSON arguments."""

    id: str
    arguments_delta: str
    type: str = field(default="tool_call_delta", init=False)


@dataclass(frozen=True)
class ToolCallEnd:
    """A tool call is fully assembled and ready to execute."""

    id: str
    name: str
    arguments: dict[str, Any]
    type: str = field(default="tool_call_end", init=False)


@dataclass(frozen=True)
class UsageInfo:
    """Token accounting for the completed model turn."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    type: str = field(default="usage", init=False)


@dataclass(frozen=True)
class StreamError:
    """A terminal error. Errors are data, never exceptions on the stream."""

    message: str
    kind: str | None = None
    type: str = field(default="error", init=False)


LLMEvent = Union[
    TextDelta,
    ReasoningDelta,
    ToolCallStart,
    ToolCallDelta,
    ToolCallEnd,
    UsageInfo,
    StreamError,
]


def serialize_llm_event(event: LLMEvent) -> dict[str, Any]:
    """Serialize an event to a plain dict (``type`` discriminator included).

    Suitable for JSON transport (SQ/EQ over a socket, a log line, an SSE
    frame). The inverse is unambiguous because every variant carries its
    ``type``.
    """
    return asdict(event)


def llm_response_to_events(response: LLMResponse) -> list[LLMEvent]:
    """Normalize a completed :class:`LLMResponse` to the event vocabulary.

    Order: reasoning → text → tool calls → usage, or a single
    :class:`StreamError` when the response is an error. This is the
    non-streaming projection; a streaming adapter would emit the same
    variants incrementally.
    """
    if response.finish_reason == "error":
        return [
            StreamError(
                message=response.content or "model error",
                kind=response.error_kind,
            )
        ]

    events: list[LLMEvent] = []
    if response.reasoning_content:
        events.append(ReasoningDelta(text=response.reasoning_content))
    if response.content:
        events.append(TextDelta(text=response.content))
    for call in response.tool_calls:
        events.append(
            ToolCallEnd(id=call.id, name=call.name, arguments=dict(call.arguments))
        )
    usage = response.usage or {}
    if usage:
        events.append(
            UsageInfo(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(
                    usage.get("total_tokens", 0)
                    or (
                        int(usage.get("prompt_tokens", 0) or 0)
                        + int(usage.get("completion_tokens", 0) or 0)
                    )
                ),
            )
        )
    return events
