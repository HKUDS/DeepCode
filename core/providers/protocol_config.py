"""Explicit provider wire choices; every compatibility field has an encoder."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

ProviderProtocol = Literal[
    "auto", "openai_chat", "openai_responses", "anthropic_messages"
]


class ProviderCompat(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    token_limit_field: Literal["max_tokens", "max_completion_tokens"] | None = None
    temperature: bool | None = None
    system_role: Literal["system", "developer", "user"] | None = None
    reasoning_field: Literal["reasoning_effort", "reasoning", "omit"] | None = None
    reasoning_content: Literal["preserve", "empty", "omit"] | None = None
    tool_message_name: bool | None = None
    parallel_tool_calls: bool | None = None

    def validate_protocol(self, protocol: str) -> None:
        supplied = self.model_dump(exclude_none=True)
        if not supplied:
            return
        if protocol == "openai_chat":
            return
        if protocol == "anthropic_messages" and set(supplied) <= {"temperature"}:
            return
        if protocol == "openai_responses" and set(supplied) <= {
            "temperature",
            "reasoning_field",
            "parallel_tool_calls",
        }:
            if self.reasoning_field != "reasoning_effort":
                return
        raise ValueError(
            "Compatibility overrides require an explicit matching protocol; the selected fields are unsupported for this protocol"
        )


def protocol_adapter(protocol: str, legacy_adapter: str) -> str:
    if protocol == "auto":
        return legacy_adapter
    return "anthropic" if protocol == "anthropic_messages" else "openai_compat"


def apply_chat_compat(
    body: dict,
    compat: ProviderCompat,
    *,
    max_tokens: int,
    temperature: float,
    reasoning_effort: str | None,
) -> dict:
    """Apply explicit overrides after the built-in model defaults, without user JSON merging."""
    if compat.token_limit_field is not None:
        body.pop("max_tokens", None)
        body.pop("max_completion_tokens", None)
        body[compat.token_limit_field] = max(1, max_tokens)
    if compat.temperature is False:
        body.pop("temperature", None)
    elif compat.temperature is True:
        body["temperature"] = temperature
    messages = [dict(message) for message in body["messages"]]
    if compat.system_role is not None:
        for message in messages:
            if message.get("role") in {"system", "developer"}:
                message["role"] = compat.system_role
    if compat.reasoning_field is not None:
        body.pop("reasoning_effort", None)
        extra = dict(body.get("extra_body", {}))
        for key in ("reasoning", "thinking", "enable_thinking", "reasoning_split"):
            extra.pop(key, None)
        effort = reasoning_effort.lower() if reasoning_effort else None
        if compat.reasoning_field == "reasoning_effort" and effort not in {
            None,
            "auto",
        }:
            body["reasoning_effort"] = effort
        elif compat.reasoning_field == "reasoning" and effort not in {None, "auto"}:
            extra["reasoning"] = (
                {"enabled": False} if effort == "none" else {"effort": effort}
            )
        if extra:
            body["extra_body"] = extra
        else:
            body.pop("extra_body", None)
    if compat.reasoning_content is not None:
        for message in messages:
            if message.get("role") == "assistant":
                if compat.reasoning_content == "empty":
                    message.setdefault("reasoning_content", "")
                elif compat.reasoning_content == "omit":
                    message.pop("reasoning_content", None)
    if compat.tool_message_name is not None:
        calls = {
            call["id"]: call.get("function", {}).get("name")
            for message in messages
            for call in message.get("tool_calls", [])
            if isinstance(call, dict) and "id" in call
        }
        for message in messages:
            if message.get("role") == "tool":
                if not compat.tool_message_name:
                    message.pop("name", None)
                elif not message.get("name"):
                    name = calls.get(message.get("tool_call_id"))
                    if not name:
                        raise ValueError(
                            "The configured protocol requires a tool result name, but its matching tool call is missing"
                        )
                    message["name"] = name
    if compat.parallel_tool_calls is not None and body.get("tools"):
        body["parallel_tool_calls"] = compat.parallel_tool_calls
    body["messages"] = messages
    return body
