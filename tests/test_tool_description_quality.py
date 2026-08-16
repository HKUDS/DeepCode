"""P1-2: tool description quality regression (GenAI lesson 11).

Lesson 11's rule: a tool description must be *specific and clear* — it decides
which tool the model picks and how well arguments are filled, and tool
definitions count against the prompt token budget. These tests pin the cheap
static proxies (length bounds, non-empty, degenerate fallback) and the MCP
remote-description sanitization.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.base import (
    _DESCRIPTION_MAX_CHARS,
    description_quality_issues,
    sanitize_description,
)
from core.mcp.tools import McpToolAdapter

# ---- description quality checks ---------------------------------------------


def test_empty_description_flagged():
    issues = description_quality_issues("")
    assert any("empty" in i for i in issues)
    issues = description_quality_issues("   ")
    assert any("empty" in i for i in issues)


def test_tiny_description_flagged():
    issues = description_quality_issues("read files")
    assert any("more specific" in i for i in issues)


def test_good_description_passes():
    good = (
        "Read a UTF-8 text file from the workspace and return its contents. "
        "Use for inspecting source files before editing."
    )
    assert description_quality_issues(good) == []


def test_overlong_description_flagged():
    long = "x" * (_DESCRIPTION_MAX_CHARS + 100)
    issues = description_quality_issues(long)
    assert any("max" in i and str(_DESCRIPTION_MAX_CHARS) in i for i in issues)


def test_sanitize_empty_falls_back_to_name():
    assert sanitize_description("", name="read") == "read tool (no description provided)"
    assert sanitize_description(None, name="write") == (
        "write tool (no description provided)"
    )


def test_sanitize_truncates_overlong_at_sentence_boundary():
    long = ("One complete sentence with enough length to be cut here. " + "y" * 5_000)
    out = sanitize_description(long, name="tool")
    assert len(out) <= _DESCRIPTION_MAX_CHARS + 32  # cap + truncation marker slack
    assert out.endswith("…[truncated]")
    # The truncation must have happened inside the long tail, not mid-sentence.
    assert "One complete sentence" in out


def test_sanitize_keeps_good_description():
    good = "A clear, specific, multi-word description of the tool."
    assert sanitize_description(good, name="t") == good


# ---- MCP remote descriptions -------------------------------------------------


def _make_adapter(description: str | None) -> McpToolAdapter:
    server = SimpleNamespace(
        server_id="srv",
        name="srv",
        source="user",
        definition=SimpleNamespace(policy_for=lambda raw: None),
    )
    connection = SimpleNamespace(
        server=server,
        call_tool=lambda name, args: "ok",
    )
    tool_definition = SimpleNamespace(
        name="remote_tool",
        description=description,
        inputSchema={
            "type": "object",
            "properties": {"p": {"type": "string"}},
        },
        annotations=None,
    )
    return McpToolAdapter(
        connection,
        tool_definition,
        visible_name="mcp__srv__remote_tool",
    )


def test_mcp_description_empty_gets_fallback():
    adapter = _make_adapter("")
    assert adapter.description == (
        "mcp__srv__remote_tool tool (no description provided)"
    )


def test_mcp_description_truncated_to_budget():
    adapter = _make_adapter("word " * 5_000)
    assert len(adapter.description) <= _DESCRIPTION_MAX_CHARS + 32
    assert adapter.description.endswith("…[truncated]")


def test_mcp_description_kept_when_quality_ok():
    good = "A remote tool that does something specific and useful for the agent."
    adapter = _make_adapter(good)
    assert adapter.description == good
