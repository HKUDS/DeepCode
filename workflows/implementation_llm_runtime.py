"""Shared LLM helpers for implementation workflows.

The implementation workflows still run their own tool loop so they can keep
their existing progress tracking and memory-compaction behavior. This module
keeps the LLM/provider glue shared between the indexed and non-indexed
variants while that larger loop remains in place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prepare_provider_tool_definitions(
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize legacy MCP tool definitions to provider function tools."""
    normalized: list[dict[str, Any]] = []
    for tool in tools or []:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            normalized.append(tool)
            continue
        name = tool.get("name")
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "input_schema",
                        {"type": "object", "properties": {}},
                    ),
                },
            }
        )
    return normalized


async def call_provider_with_legacy_tools(
    provider,
    *,
    system_message: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    validate_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    logger: Any = None,
) -> dict[str, Any]:
    """Call a DeepCode provider and return the legacy workflow response shape."""
    validated_messages = validate_messages(messages)
    if not validated_messages:
        validated_messages = [
            {"role": "user", "content": "Please continue implementing code"}
        ]

    provider_messages: list[dict[str, Any]] = []
    if system_message:
        provider_messages.append({"role": "system", "content": system_message})
    provider_messages.extend(validated_messages)

    response = await provider.chat_with_retry(
        messages=provider_messages,
        tools=prepare_provider_tool_definitions(tools),
        model=provider.get_default_model(),
        max_tokens=max_tokens,
        temperature=0.2,
        retry_mode="persistent",
    )
    if response.finish_reason == "error":
        raise RuntimeError(response.content or "LLM provider returned an error")

    token_usage = dict(response.usage or {})
    if token_usage and logger:
        logger.info("Token usage: %s", token_usage)

    return {
        "content": response.content or "",
        "tool_calls": [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "input": tool_call.arguments,
            }
            for tool_call in response.tool_calls
        ],
        "token_usage": token_usage,
        "cost": 0.0,
    }
