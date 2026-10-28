"""Anthropic prompt caching — clear-then-finalize at the conversation frontier.

Every test drives ``AnthropicProvider._build_kwargs`` so the input messages go
through the provider's own conversion exactly like a live request; assertions
run on the converted Anthropic-format payload inside ``kwargs``.

Contract under test:

- the message breakpoint is written clear-then-finalize: every stale
  ``cache_control`` is stripped from the converted list, then ONE fresh
  marker lands on the LATEST message — the previous fixed ``messages[-2]``
  write point missed the frontier whenever the tail shape changed;
- a trailing system note never steals the frontier (it folds into the system
  prompt, and the marker stays on the last real message);
- the per-request marker budget is at most 4 (system 1 + tools <= 2 +
  message frontier 1), with tool markers on the last builtin and the MCP tail;
- a string system prompt is wrapped into exactly one marked block;
- ``supports_caching=False`` emits no markers at all, and empty tool lists
  never produce a ``tools`` kwarg.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.anthropic import AnthropicProvider


def _provider() -> AnthropicProvider:
    provider = AnthropicProvider(api_key="test-key", default_model="claude-sonnet-4-6")
    provider.reasoning_supported = False
    return provider


def _build(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    supports_caching: bool = True,
) -> dict[str, Any]:
    """Run the real kwargs path: OpenAI-format in, Anthropic-format payload out."""
    return _provider()._build_kwargs(
        messages,
        tools,
        "claude-sonnet-4-6",
        1024,
        0.0,
        None,
        None,
        supports_caching=supports_caching,
    )


def _marked_indices(messages: list[dict[str, Any]]) -> list[int]:
    """Indices of messages carrying a cache_control marker (key or block)."""
    marked: list[int] = []
    for i, msg in enumerate(messages):
        if "cache_control" in msg:
            marked.append(i)
            continue
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and "cache_control" in b for b in content
        ):
            marked.append(i)
    return marked


def _count_markers(*structures: Any) -> int:
    """How many cache_control markers live across the given structures."""
    total = 0
    for items in structures:
        if not items:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if "cache_control" in item:
                total += 1
            content = item.get("content")
            if isinstance(content, list):
                total += sum(
                    1 for b in content if isinstance(b, dict) and "cache_control" in b
                )
    return total


def _conversation() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "read the file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file body"},
        {"role": "user", "content": "thanks"},
    ]


def _tools(*names: str) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": name, "parameters": {}}}
        for name in names
    ]


# ---------------------------------------------------------------------------
# Frontier placement (the change under review)
# ---------------------------------------------------------------------------


def test_frontier_marker_lands_on_latest_message() -> None:
    kwargs = _build(_conversation())
    out = kwargs["messages"]
    assert len(out) >= 3
    # ONE message-level breakpoint, on the LATEST message — not the fixed
    # messages[-2] write point, which marked the tool result instead.
    assert _marked_indices(out) == [len(out) - 1]


def test_trailing_system_note_does_not_steal_the_frontier() -> None:
    kwargs = _build(_conversation() + [{"role": "system", "content": "[note]"}])
    out = kwargs["messages"]
    # The note folds into the system prompt; the frontier marker stays on the
    # last real conversation message.
    assert "[note]" in str(kwargs.get("system"))
    assert _marked_indices(out) == [len(out) - 1]


def test_stale_markers_are_stripped_and_one_frontier_marker_remains() -> None:
    messages = _conversation()
    # A replayed list: a stale marker on an early user message, exactly the
    # leftover state the old write point could never clean up.
    messages[0] = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "read the file",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }

    kwargs = _build(messages)
    out = kwargs["messages"]

    # Exactly one message-level breakpoint, on the LATEST message.
    assert _marked_indices(out) == [len(out) - 1]
    # The stale marker on the early user message is gone after conversion.
    first = out[0]
    assert isinstance(first.get("content"), list)
    assert all("cache_control" not in b for b in first["content"])


# ---------------------------------------------------------------------------
# Marker budget
# ---------------------------------------------------------------------------


def test_marker_budget_never_exceeds_four() -> None:
    kwargs = _build(
        _conversation(),
        _tools(
            "read",
            "bash",
            "edit",
            "mcp__github__createIssue",
            "mcp__github__listIssues",
        ),
    )
    total = _count_markers(
        kwargs.get("system") if isinstance(kwargs.get("system"), list) else None,
        kwargs["messages"],
        kwargs.get("tools"),
    )
    assert total <= 4
    assert total >= 3  # system 1 + message 1 + at least one tool


def test_tool_markers_cover_last_builtin_and_tail() -> None:
    kwargs = _build(_conversation(), _tools("read", "bash", "mcp__ghidra__decompile"))
    out_tools = kwargs.get("tools") or []
    marked = [i for i, t in enumerate(out_tools) if "cache_control" in t]
    # Last builtin (bash, index 1) and the MCP tail (index 2), never more.
    assert marked == [1, 2]


# ---------------------------------------------------------------------------
# System + non-caching paths
# ---------------------------------------------------------------------------


def test_string_system_becomes_one_marked_block() -> None:
    kwargs = _build(
        [{"role": "system", "content": "base instructions"}] + _conversation()
    )
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "base instructions",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_no_markers_when_caching_disabled() -> None:
    kwargs = _build(
        [{"role": "system", "content": "base"}] + _conversation(),
        _tools("read", "bash", "edit"),
        supports_caching=False,
    )
    system = kwargs.get("system")
    assert system == "base"  # plain string, never block-wrapped on this path
    assert _count_markers(None, kwargs["messages"], kwargs.get("tools")) == 0
    assert "cache_control" not in system


def test_tools_absent_when_none_or_empty() -> None:
    assert "tools" not in _build(_conversation(), None)
    assert "tools" not in _build(_conversation(), [])
