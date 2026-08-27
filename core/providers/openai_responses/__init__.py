"""Shared helpers for OpenAI Responses API providers (Codex, Azure OpenAI)."""

from core.providers.openai_responses.converters import (
    convert_messages,
    convert_tools,
    convert_user_message,
    split_tool_call_id,
)
from core.providers.openai_responses.parsing import (
    FINISH_REASON_MAP,
    consume_sdk_stream,
    consume_sse,
    iter_sse,
    map_finish_reason,
    parse_response_output,
)

__all__ = [
    "FINISH_REASON_MAP",
    "consume_sdk_stream",
    "consume_sse",
    "convert_messages",
    "convert_tools",
    "convert_user_message",
    "iter_sse",
    "map_finish_reason",
    "parse_response_output",
    "split_tool_call_id",
]
