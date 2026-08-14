"""Tests for request_user_input (C1 — the agent can ask the user)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.user_input import RequestUserInputTool  # noqa: E402


def _run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


def test_sync_ask_returns_answers():
    seen = {}

    def ask(question, options):
        seen["q"], seen["opts"] = question, options
        return "TOML"

    tool = RequestUserInputTool(ask)
    out = _run(
        tool,
        questions=[
            {"question": "Which config format?", "options": ["JSON", "YAML", "TOML"]}
        ],
    )
    assert "Which config format?" in out and "TOML" in out
    assert seen["opts"] == ["JSON", "YAML", "TOML"]


def test_async_ask_is_awaited():
    async def ask(question, options):
        return "async-answer"

    out = _run(RequestUserInputTool(ask), questions=[{"question": "q?"}])
    assert "async-answer" in out


def test_multiple_questions_capped_at_three():
    calls = []

    def ask(q, o):
        calls.append(q)
        return "ok"

    _run(
        RequestUserInputTool(ask),
        questions=[{"question": f"q{i}"} for i in range(5)],
    )
    assert len(calls) == 3  # schema says <=3; extras are dropped


def test_no_channel_degrades_gracefully():
    out = _run(RequestUserInputTool(None), questions=[{"question": "q?"}])
    assert "no interactive user" in out.lower()
    assert "assumption" in out.lower()


def test_bad_input():
    out = _run(RequestUserInputTool(lambda q, o: "x"), questions=[])
    assert "non-empty" in out
    out2 = _run(RequestUserInputTool(lambda q, o: "x"), questions=[{"header": "h"}])
    assert "question is required" in out2


def test_wiring_only_when_channel_present():
    from core.harness.tools import default_coding_tools

    # headless: no ask_user -> no request_user_input tool
    assert "request_user_input" not in set(default_coding_tools("/tmp").tool_names)
    # interactive: ask_user provided -> tool present
    reg = default_coding_tools("/tmp", ask_user=lambda q, o: "y")
    assert "request_user_input" in set(reg.tool_names)
