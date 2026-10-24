"""Anthropic prompt caching — clear-then-finalize + cacheHint sections.

Contract under test:

- calling ``_apply_cache_control`` directly never raises NameError (the
  historical bug class: an unqualified helper reference that only blew up
  at request time on a live provider);
- the message breakpoint is written clear-then-finalize: every stale
  ``cache_control`` is stripped first, then ONE fresh marker lands on the
  latest non-system message — a replayed or reused request list can never
  carry a leftover breakpoint pinning the cache at the wrong offset;
- the per-request marker budget is at most 4 (system 1 + tools ≤ 2 +
  message frontier 1);
- block-list system prompts carry a private ``cacheHint`` of stable /
  dynamic: the breakpoint lands on the LAST STABLE block so edits to the
  dynamic tail never bust the stable-prefix cache, and the private hint
  key is stripped before send (it must never reach the Anthropic API);
- a plain-string system is wrapped into a single marked block; unhinted
  block lists keep the previous last-block behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.providers.anthropic import AnthropicProvider


def _markers(*lists: list[dict[str, Any]] | None) -> int:
    """How many cache_control markers live across the given structures."""
    total = 0
    for items in lists:
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


def _stale_marker() -> dict[str, str]:
    return {"type": "ephemeral"}


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


# ---------------------------------------------------------------------------
# Bare-name regression + clear-then-finalize
# ---------------------------------------------------------------------------


def test_apply_cache_control_is_directly_callable() -> None:
    """The bare-name regression: classmethod-level use must not NameError."""
    system, messages, tools = AnthropicProvider._apply_cache_control(
        "base instructions", _conversation(), None
    )
    assert isinstance(system, list)
    assert len(messages) == 4
    assert tools is None


def test_stale_markers_are_stripped_and_one_frontier_marker_remains() -> None:
    messages = _conversation()
    # A replayed list: markers on an early user message AND on the tool
    # result, exactly the stale state the old write point could leave behind.
    messages[0] = {
        "role": "user",
        "content": [
            {"type": "text", "text": "read the file", "cache_control": _stale_marker()}
        ],
    }
    messages[2] = {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": [
            {"type": "text", "text": "file body", "cache_control": _stale_marker()}
        ],
        "cache_control": _stale_marker(),
    }

    _, out, _ = AnthropicProvider._apply_cache_control("sys", messages, None)

    # Exactly one message-level breakpoint, on the LATEST non-system message.
    marked = [
        i
        for i, m in enumerate(out)
        if "cache_control" in m
        or (
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
        )
    ]
    assert marked == [3]  # final user message, string content -> wrapped block
    # The stale message-level key on the tool result is gone.
    assert "cache_control" not in out[2]
    # String content at the frontier is block-wrapped, not mutated in place.
    assert out[3]["content"] == [
        {
            "type": "text",
            "text": "thanks",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert messages[3]["content"] == "thanks"  # caller list untouched


def test_trailing_system_note_does_not_steal_the_frontier() -> None:
    messages = _conversation() + [{"role": "system", "content": "[note]"}]
    _, out, _ = AnthropicProvider._apply_cache_control("sys", messages, None)
    # Frontier marker sits on the last NON-system message, not the note.
    # String content is block-wrapped, so check both message-level keys and
    # content-block markers.
    marked = [
        i
        for i, m in enumerate(out)
        if "cache_control" in m
        or (
            isinstance(m.get("content"), list)
            and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
        )
    ]
    assert marked == [3]


def test_all_system_history_places_no_message_marker() -> None:
    messages = [{"role": "system", "content": "only notes"}]
    _, out, _ = AnthropicProvider._apply_cache_control("sys", messages, None)
    assert _markers(out) == 0


# ---------------------------------------------------------------------------
# Marker budget
# ---------------------------------------------------------------------------


def test_marker_budget_never_exceeds_four() -> None:
    tools = [
        {"name": "read", "description": "", "input_schema": {}},
        {"name": "bash", "description": "", "input_schema": {}},
        {"name": "edit", "description": "", "input_schema": {}},
        {"name": "mcp__github__createIssue", "description": "", "input_schema": {}},
        {"name": "mcp__github__listIssues", "description": "", "input_schema": {}},
    ]
    system, out, out_tools = AnthropicProvider._apply_cache_control(
        "base instructions", _conversation(), tools
    )
    total = _markers(system if isinstance(system, list) else None, out, out_tools)
    assert total <= 4
    assert total >= 3  # system 1 + message 1 + at least one tool


def test_tool_markers_cover_last_builtin_and_tail() -> None:
    tools = [
        {"name": "read", "description": "", "input_schema": {}},
        {"name": "bash", "description": "", "input_schema": {}},
        {"name": "mcp__ghidra__decompile", "description": "", "input_schema": {}},
    ]
    _, _, out_tools = AnthropicProvider._apply_cache_control(
        "sys", _conversation(), tools
    )
    marked = [i for i, t in enumerate(out_tools or []) if "cache_control" in t]
    # Last builtin (bash, index 1) and the MCP tail (index 2), never more.
    assert marked == [1, 2]


def test_empty_or_none_tools_pass_through() -> None:
    _, _, no_tools = AnthropicProvider._apply_cache_control(
        "sys", _conversation(), None
    )
    _, _, empty_tools = AnthropicProvider._apply_cache_control(
        "sys", _conversation(), []
    )
    assert no_tools is None
    assert empty_tools == []


# ---------------------------------------------------------------------------
# System breakpoint + cacheHint sections
# ---------------------------------------------------------------------------


def test_string_system_becomes_one_marked_block() -> None:
    system, _, _ = AnthropicProvider._apply_cache_control(
        "base instructions", _conversation(), None
    )
    assert system == [
        {
            "type": "text",
            "text": "base instructions",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_cache_hint_stable_boundary_wins_and_hints_are_stripped() -> None:
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "persona", "cacheHint": "stable"},
        {"type": "text", "text": "collab preamble", "cacheHint": "stable"},
        {"type": "text", "text": "project docs", "cacheHint": "dynamic"},
        {"type": "text", "text": "memory index", "cacheHint": "dynamic"},
    ]
    system, _, _ = AnthropicProvider._apply_cache_control(
        system_blocks, _conversation(), None
    )
    assert isinstance(system, list)
    # Breakpoint on the LAST STABLE block (index 1), not the list tail.
    marked = [i for i, b in enumerate(system) if "cache_control" in b]
    assert marked == [1]
    # The private hint key never survives to the API payload.
    assert all("cacheHint" not in b for b in system)
    # Block order and text are preserved.
    assert [b["text"] for b in system] == [
        "persona",
        "collab preamble",
        "project docs",
        "memory index",
    ]


def test_unhinted_block_list_keeps_last_block_behavior() -> None:
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]
    system, _, _ = AnthropicProvider._apply_cache_control(
        system_blocks, _conversation(), None
    )
    assert isinstance(system, list)
    assert [i for i, b in enumerate(system) if "cache_control" in b] == [1]


def test_stale_system_markers_are_stripped_before_finalize() -> None:
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "a", "cache_control": _stale_marker()},
        {"type": "text", "text": "b", "cache_control": _stale_marker()},
    ]
    system, _, _ = AnthropicProvider._apply_cache_control(
        system_blocks, _conversation(), None
    )
    assert isinstance(system, list)
    assert [i for i, b in enumerate(system) if "cache_control" in b] == [1]


def test_empty_system_passes_through_unchanged() -> None:
    empty_list, _, _ = AnthropicProvider._apply_cache_control([], _conversation(), None)
    empty_str, _, _ = AnthropicProvider._apply_cache_control("", _conversation(), None)
    assert empty_list == []
    assert empty_str == ""


def test_strip_system_cache_hints_is_copy_on_write() -> None:
    blocks = [
        {"type": "text", "text": "a", "cacheHint": "stable"},
        {"type": "text", "text": "b", "cacheHint": "dynamic"},
    ]
    stripped = AnthropicProvider._strip_system_cache_hints(blocks)
    assert all("cacheHint" not in b for b in stripped)
    # Input untouched (copy-on-write): the caller's history keeps its hint.
    assert all("cacheHint" in b for b in blocks)
    # Idempotent + str passthrough.
    assert AnthropicProvider._strip_system_cache_hints(stripped) == stripped
    assert AnthropicProvider._strip_system_cache_hints("plain") == "plain"
