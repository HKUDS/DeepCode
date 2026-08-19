"""Deterministic model-visible naming without losing raw MCP identities."""

from __future__ import annotations

import hashlib
import os
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


def server_allowed(server_id: str, server_name: str | None = None) -> bool:
    """P1-9 (GenAI lesson 13): MCP server allowlist (supply-chain hardening).

    Remote MCP servers are the harness's widest third-party exposure surface
    (lesson 13: supply-chain vulnerabilities — a compromised server can
    register arbitrary tools). ``DEEPCODE_MCP_SERVER_ALLOWLIST`` is a
    comma-separated list of server ids *or* names; only matching servers are
    registered. Empty/unset = all servers allowed (the default, preserving
    current behavior). ``server_name`` is checked as an alias so users can
    allowlist by the name they configured, not just the generated id.
    """
    raw = os.environ.get("DEEPCODE_MCP_SERVER_ALLOWLIST", "").strip()
    if not raw:
        return True
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    if not allowed:
        return True
    if server_id in allowed:
        return True
    return bool(server_name) and server_name in allowed


def allowlist_env() -> str:
    """The raw allowlist env value (for tests / diagnostics)."""
    return os.environ.get("DEEPCODE_MCP_SERVER_ALLOWLIST", "").strip()


__all__ = [
    "MAX_TOOL_NAME_LENGTH",
    "allowlist_env",
    "server_allowed",
    "visible_tool_name",
]
