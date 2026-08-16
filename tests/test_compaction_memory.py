"""P1-5: compaction-as-memory (GenAI lesson 15).

Compressed sessions must stay retrievable: the handoff summary is deposited
into the memory vault with anchor metadata (session key, phase, timestamp,
sizes) instead of vanishing when the history is replaced. Tests cover the
memory-note writer and the runner's sink trigger (auto + manual).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.runner import AgentRunSpec
from core.agent_runtime.tools.registry import ToolRegistry
from core.harness.memory import (
    _COMPACTION_NOTE,
    compaction_sink_enabled,
    write_compaction_summary,
)
from core.providers.base import LLMResponse

# ---- writer ----------------------------------------------------------------


def test_write_creates_note_with_summary_and_anchor(tmp_path):
    write_compaction_summary(
        tmp_path,
        "Handoff summary: implemented the parser.",
        anchor={"session_key": "s1", "phase": "auto", "at": "2026-08-16T10:00:00"},
    )
    note = tmp_path / ".deepcode" / "memory" / _COMPACTION_NOTE
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "Handoff summary: implemented the parser." in text
    assert "session_key=s1" in text
    assert "phase=auto" in text
    assert "## Compaction" in text


def test_write_appends_multiple_entries(tmp_path):
    write_compaction_summary(tmp_path, "first summary", anchor={"phase": "auto"})
    write_compaction_summary(tmp_path, "second summary", anchor={"phase": "manual"})
    note = tmp_path / ".deepcode" / "memory" / _COMPACTION_NOTE
    text = note.read_text(encoding="utf-8")
    assert text.count("## Compaction") == 2
    assert "first summary" in text and "second summary" in text


def test_write_without_anchor_still_lands(tmp_path):
    write_compaction_summary(tmp_path, "bare summary")
    note = tmp_path / ".deepcode" / "memory" / _COMPACTION_NOTE
    assert "bare summary" in note.read_text(encoding="utf-8")


def test_write_empty_summary_noop(tmp_path):
    write_compaction_summary(tmp_path, "")
    write_compaction_summary(tmp_path, "   ")
    assert not (tmp_path / ".deepcode" / "memory" / _COMPACTION_NOTE).exists()


def test_write_never_raises_on_bad_workspace(tmp_path):
    # A path that cannot be created (a file in the way) must not raise.
    blocker = tmp_path / ".deepcode"
    blocker.write_text("i am a file", encoding="utf-8")
    write_compaction_summary(tmp_path, "summary")  # should be swallowed
    assert True  # reached = never raised


def test_compaction_sink_env_opt_out(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPCODE_COMPACTION_MEMORY", "0")
    assert compaction_sink_enabled() is False
    write_compaction_summary(tmp_path, "should not land")
    assert not (tmp_path / ".deepcode" / "memory" / _COMPACTION_NOTE).exists()


def test_compaction_sink_env_default_on(monkeypatch):
    monkeypatch.delenv("DEEPCODE_COMPACTION_MEMORY", raising=False)
    assert compaction_sink_enabled() is True


# ---- runner sink trigger ----------------------------------------------------


class _SinkCapture:
    def __init__(self):
        self.calls = []

    def __call__(self, summary, anchor):
        self.calls.append((summary, dict(anchor)))


class _Provider:
    async def chat_with_retry(self, **kwargs):
        return LLMResponse(content="A useful handoff summary.", finish_reason="stop")

    generation = type("G", (), {"max_tokens": 4096})()


def _messages() -> list[dict]:
    # Sized so the handoff summary genuinely shrinks the history (the
    # convergence rule rejects a summary that does not reduce volume).
    return [
        {"role": "user", "content": "query one " + "w" * 400},
        {"role": "assistant", "content": "step one " + "x" * 400},
        {"role": "user", "content": "query two " + "w" * 400},
        {"role": "assistant", "content": "step two " + "x" * 400},
        {"role": "user", "content": "query three " + "w" * 400},
    ]


def _spec(**kw) -> AgentRunSpec:
    base = {
        "initial_messages": [],
        "tools": ToolRegistry(),
        "model": "m",
        "max_iterations": 1,
        "max_tool_result_chars": 1000,
        "session_key": "sess-1",
    }
    base.update(kw)
    return AgentRunSpec(**base)


def test_runner_notifies_sink_on_manual_compact():
    import asyncio

    from core.agent_runtime.runner import AgentRunner

    sink = _SinkCapture()
    runner = AgentRunner(_Provider())
    spec = _spec(session_key="sess-1", compaction_summary_sink=sink)
    messages = _messages()
    compacted, reason = asyncio.run(runner.compact_history(spec, messages))
    assert compacted is not None and reason == "compacted"
    assert len(sink.calls) == 1
    summary, anchor = sink.calls[0]
    assert "handoff summary" in summary
    assert anchor["session_key"] == "sess-1"
    assert anchor["phase"] == "manual"
    assert anchor["messages_before"] == len(messages)


def test_runner_sink_absent_is_noop():
    import asyncio

    from core.agent_runtime.runner import AgentRunner

    runner = AgentRunner(_Provider())
    spec = _spec(session_key="sess-2", compaction_summary_sink=None)
    messages = _messages()
    compacted, _reason = asyncio.run(runner.compact_history(spec, messages))
    assert compacted is not None  # compaction itself still works


def test_runner_sink_failure_is_swallowed():
    import asyncio

    from core.agent_runtime.runner import AgentRunner

    def _boom(summary, anchor):
        raise RuntimeError("sink exploded")

    runner = AgentRunner(_Provider())
    spec = _spec(session_key="sess-3", compaction_summary_sink=_boom)
    messages = _messages()
    compacted, reason = asyncio.run(runner.compact_history(spec, messages))
    assert compacted is not None and reason == "compacted"
