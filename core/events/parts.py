"""Structured message parts (L1).

The kernel works in OpenAI-style message dicts (``{"role": ..., "content":
..., "tool_calls": [...]}``). That shape is fine for the wire but opaque for
a UI, persistence, replay, or cost accounting — everything has to re-parse
strings. The *parts* model is the structured projection: a conversation is a
list of :class:`Message`, each a list of typed :class:`Part` values, where a
tool call is a first-class :class:`ToolPart` carrying a state machine
(``pending → running → completed | error``) instead of a pair of loosely
related dicts.

:func:`messages_to_parts` converts the kernel's message dicts (e.g.
``AgentRunResult.messages``) into this model, pairing each assistant
``tool_calls`` entry with the ``role: "tool"`` result that fulfilled it.

Scope note: only part types with a *real producer today* are defined
(text / reasoning / tool). ``step`` / ``patch`` parts are intentionally
absent until their producers exist (turn boundaries and git snapshots land
in P2) — an empty variant with no producer would be speculative dead weight.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Union


class ToolState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class TextPart:
    text: str
    type: str = field(default="text", init=False)


@dataclass
class ReasoningPart:
    text: str
    type: str = field(default="reasoning", init=False)


@dataclass
class ToolPart:
    """A tool call as a first-class object with a lifecycle state."""

    id: str
    name: str
    arguments: dict[str, Any]
    state: ToolState = ToolState.PENDING
    result: str | None = None
    error: str | None = None
    type: str = field(default="tool", init=False)


Part = Union[TextPart, ReasoningPart, ToolPart]


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    parts: list[Part]


# Prefixes the kernel uses to mark a tool result as an error (errors-as-data).
_ERROR_PREFIXES = ("Error", "Error:", "permission denied")


def _looks_like_error(content: str) -> bool:
    stripped = content.lstrip()
    return any(stripped.startswith(p) for p in _ERROR_PREFIXES) or (
        "permission denied" in stripped[:40]
    )


def _tool_call_fields(raw: Any) -> tuple[str, str, dict[str, Any]] | None:
    """Extract ``(id, name, arguments)`` from an OpenAI-style tool_call dict."""
    if not isinstance(raw, dict):
        return None
    call_id = str(raw.get("id") or "")
    fn = raw.get("function")
    if not isinstance(fn, dict):
        return None
    name = str(fn.get("name") or "")
    args_raw = fn.get("arguments")
    if isinstance(args_raw, dict):
        arguments = args_raw
    elif isinstance(args_raw, str) and args_raw.strip():
        try:
            arguments = json.loads(args_raw)
        except (json.JSONDecodeError, ValueError):
            arguments = {"__raw__": args_raw}
    else:
        arguments = {}
    return call_id, name, arguments


def messages_to_parts(messages: list[dict[str, Any]]) -> list[Message]:
    """Project kernel message dicts into the structured parts model.

    ``role: "tool"`` messages are absorbed into the matching assistant
    :class:`ToolPart` (by ``tool_call_id``) rather than becoming their own
    message, so the tool call and its result read as one object.
    """
    # First pass: collect tool results by call id.
    results: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool":
            tid = str(msg.get("tool_call_id") or "")
            if tid:
                content = msg.get("content")
                results[tid] = content if isinstance(content, str) else str(content)

    out: list[Message] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            continue  # absorbed above
        if role in ("system", "user"):
            content = msg.get("content")
            text = content if isinstance(content, str) else str(content or "")
            out.append(Message(role=role, parts=[TextPart(text=text)]))
            continue
        if role == "assistant":
            parts: list[Part] = []
            reasoning = msg.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                parts.append(ReasoningPart(text=reasoning))
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(TextPart(text=content))
            for raw in msg.get("tool_calls") or []:
                fields = _tool_call_fields(raw)
                if fields is None:
                    continue
                call_id, name, arguments = fields
                result = results.get(call_id)
                if result is None:
                    state, error = ToolState.PENDING, None
                elif _looks_like_error(result):
                    state, error = ToolState.ERROR, result
                else:
                    state, error = ToolState.COMPLETED, None
                parts.append(
                    ToolPart(
                        id=call_id,
                        name=name,
                        arguments=arguments,
                        state=state,
                        result=result,
                        error=error,
                    )
                )
            out.append(Message(role="assistant", parts=parts))
            continue
        # Unknown role: keep it as text so nothing is silently dropped.
        out.append(
            Message(role=str(role or "unknown"), parts=[TextPart(text=str(msg))])
        )
    return out


def serialize_part(part: Part) -> dict[str, Any]:
    # asdict reads declared fields (incl. the ``type`` discriminator, which
    # is a class-level default and absent from ``__dict__``).
    data = asdict(part)
    if isinstance(part, ToolPart):
        data["state"] = part.state.value
    return data


def serialize_message(message: Message) -> dict[str, Any]:
    return {"role": message.role, "parts": [serialize_part(p) for p in message.parts]}
