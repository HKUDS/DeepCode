"""Deterministic model-visible naming without losing raw MCP identities."""

from __future__ import annotations

import hashlib
import re

MAX_TOOL_NAME_LENGTH = 64
_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


def visible_tool_name(
    server_id: str,
    raw_tool_name: str,
    *,
    used: set[str],
) -> str:
    server = _segment(server_id)
    tool = _segment(raw_tool_name)
    candidate = f"mcp__{server}__{tool}"
    if len(candidate) <= MAX_TOOL_NAME_LENGTH and candidate not in used:
        used.add(candidate)
        return candidate

    digest = hashlib.sha256(f"{server_id}\0{raw_tool_name}".encode()).hexdigest()[:10]
    suffix = f"__{digest}"
    prefix = candidate[: MAX_TOOL_NAME_LENGTH - len(suffix)].rstrip("_")
    candidate = f"{prefix}{suffix}"
    counter = 1
    while candidate in used:
        numbered = f"_{counter}"
        candidate = f"{prefix[: MAX_TOOL_NAME_LENGTH - len(suffix) - len(numbered)]}{suffix}{numbered}"
        counter += 1
    used.add(candidate)
    return candidate


def _segment(value: str) -> str:
    cleaned = _INVALID.sub("_", value).strip("_")
    return cleaned or "unnamed"


__all__ = ["MAX_TOOL_NAME_LENGTH", "visible_tool_name"]
