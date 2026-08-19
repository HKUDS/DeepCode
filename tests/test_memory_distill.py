"""Tests for the P0-2 memory distillation bridge (DeepCode → cerebellum)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_distill import (
    dialogue_from_history,
    memory_distill_enabled,
)

# ---- dialogue extraction ----------------------------------------------------


def test_empty_history_yields_empty():
    assert dialogue_from_history([]) == ""


def test_user_assistant_and_tool_lines():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": "hi there"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "name": "read_file", "arguments": {"file_path": "a.py"}}
            ],
        },
        {"role": "tool", "tool_call_id": "1", "content": "def foo(): pass"},
    ]
    text = dialogue_from_history(history)
    assert "[user] hello" in text
    assert "[assistant] hi there" in text
    assert 'read_file({"file_path": "a.py"})' in text
    assert "[tool] result: def foo(): pass" in text


def test_string_content_and_nested_text():
    history = [
        {"role": "user", "content": "plain string"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "nested"},
                {"type": "text", "text": " parts"},
            ],
        },
    ]
    text = dialogue_from_history(history)
    assert "[user] plain string" in text
    assert "[assistant] nested" in text and "parts" in text


def test_long_messages_truncated():
    history = [{"role": "user", "content": "A" * 5000}]
    text = dialogue_from_history(history)
    assert len(text) < 700  # MAX_MSG_CHARS 600 + marker overhead
    assert "...[truncated]..." in text


def test_context_cap_enforced():
    big = [{"role": "user", "content": "word " * 3000} for _ in range(50)]
    text = dialogue_from_history(big)
    assert len(text) <= 4000  # MAX_CONTEXT_CHARS


def test_non_dict_and_garbage_ignored():
    history = [{"role": "user", "content": "ok"}, "not-a-dict", {"role": "tool"}]
    text = dialogue_from_history(history)
    assert "[user] ok" in text
    assert "not-a-dict" not in text


# ---- env switch -------------------------------------------------------------


def test_distill_enabled_by_default(monkeypatch):
    monkeypatch.delenv("DEEPCODE_MEMORY_DISTILL", raising=False)
    assert memory_distill_enabled() is True


def test_distill_env_disable(monkeypatch):
    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv("DEEPCODE_MEMORY_DISTILL", value)
        assert memory_distill_enabled() is False


def test_distill_env_enable(monkeypatch):
    for value in ("1", "true", "on", "yes"):
        monkeypatch.setenv("DEEPCODE_MEMORY_DISTILL", value)
        assert memory_distill_enabled() is True
