"""Runtime constants and small helpers used by the agent runner."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from loguru import logger

from core.agent_runtime.helpers import stringify_text_blocks

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

EMPTY_FINAL_RESPONSE_MESSAGE = (
    "I completed the tool steps but couldn't produce a final answer. "
    "Please try again or narrow the task."
)

FINALIZATION_RETRY_PROMPT = (
    "Please provide your response to the user based on the conversation above."
)

LENGTH_RECOVERY_PROMPT = (
    "Output limit reached. Continue exactly where you left off "
    "— no recap, no apology. Break remaining work into smaller steps if needed."
)


def empty_tool_result_message(tool_name: str) -> str:
    return f"({tool_name} completed with no output)"


# A dict of one of these types is already a message content block.
_CONTENT_BLOCK_TYPES = frozenset(
    {"text", "image", "image_url", "input_text", "input_image", "output_text"}
)


def encode_structured_tool_result(content: Any) -> Any:
    """Send a tool's structured value to the model as JSON text.

    A dict that is not itself a content block (the Goal tools return one) is
    data. Every provider wire takes a tool result as text or as a list of
    content blocks; a bare dict used to reach them wrapped as ``[dict]``,
    which none of them documents.
    """
    if isinstance(content, dict) and content.get("type") not in _CONTENT_BLOCK_TYPES:
        return json.dumps(content, ensure_ascii=False, default=str)
    return content


def ensure_nonempty_tool_result(tool_name: str, content: Any) -> Any:
    if content is None:
        return empty_tool_result_message(tool_name)
    if isinstance(content, str) and not content.strip():
        return empty_tool_result_message(tool_name)
    if isinstance(content, list):
        if not content:
            return empty_tool_result_message(tool_name)
        text_payload = stringify_text_blocks(content)
        if text_payload is not None and not text_payload.strip():
            return empty_tool_result_message(tool_name)
    return content


def is_blank_text(content: str | None) -> bool:
    return content is None or not content.strip()


def build_finalization_retry_message() -> dict[str, str]:
    return {"role": "user", "content": FINALIZATION_RETRY_PROMPT}


def build_length_recovery_message() -> dict[str, str]:
    return {"role": "user", "content": LENGTH_RECOVERY_PROMPT}


def external_lookup_signature(tool_name: str, arguments: dict[str, Any]) -> str | None:
    if tool_name != "web_fetch":
        return None
    url = str(arguments.get("url") or "").strip()
    if not url:
        return None
    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()
    return f"web_fetch:{digest}"


def repeated_external_lookup_error(
    tool_name: str,
    arguments: dict[str, Any],
    seen_counts: dict[str, int],
) -> str | None:
    signature = external_lookup_signature(tool_name, arguments)
    if signature is None:
        return None
    count = seen_counts.get(signature, 0) + 1
    seen_counts[signature] = count
    if count <= _MAX_REPEAT_EXTERNAL_LOOKUPS:
        return None
    logger.warning(
        "Blocking repeated external lookup {} on attempt {}",
        signature[:160],
        count,
    )
    return (
        "Error: repeated external lookup blocked. "
        "Use the result you already have, or try a meaningfully different URL."
    )
