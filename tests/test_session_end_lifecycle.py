"""SessionEnd lifecycle + PreCompact checkpoint + MCP list-discovery e2e tests.

Covers the lifecycle contracts introduced by the SessionEnd / PreCompact work:

- ``SessionEnd`` fires exactly once at real session termination
  (``AgentSession.submit(Shutdown)``), never per turn; the session-exit reason
  doubles as the matcher input (``shutdown`` / ``interrupted`` / ``error``);
  a failing hook is non-fatal and never blocks ``ShutdownComplete``.
- The ``PreCompact`` hook's ``additional_contexts`` survive a successful
  compaction as a single bounded, provider-agnostic user message, and are
  absent when the hook blocks or summarization fails.
- The deepcode-hooks MCP ``hooks_config.json`` list format is only accepted
  from the ``user-mcp`` source, supports ``priority`` ordering, skips disabled
  entries, warns on invalid entries, and honours timeouts and event aliases.

Hooks are exercised as REAL subprocesses (``sh -lc`` commands that echo JSON or
exit with a code), matching ``test_hooks.py`` so we test the true execution
path, not a mock of it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.hooks.discovery import Handler, discover_hooks  # noqa: E402
from core.harness.hooks.engine import HooksEngine  # noqa: E402
from core.events.protocol import Shutdown, ShutdownComplete, UserInput  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None, reason="POSIX shell required"
)


def _handler(event, command, *, matcher=None, order=0, timeout=30):
    return Handler(
        event_name=event,
        matcher=matcher,
        command=command,
        timeout_sec=timeout,
        source="project",
        source_path="/tmp/hooks.json",
        display_order=order,
    )


def _engine(handlers, cwd="/tmp"):
    return HooksEngine(handlers, cwd, session_id="sess-1")


def _session(hooks_engine):
    from core.events.session import AgentSession

    return AgentSession(
        provider=None,
        tools=_FakeTools(),
        model="m",
        hooks_engine=hooks_engine,
        context_window_tokens=8000,
    )


class _FakeTools:
    """Minimal tool registry: records the params each tool ran with."""

    def __init__(self):
        self.calls = []

    def get_definitions(self):
        return []

    async def execute(self, name, params):
        self.calls.append((name, params))
        return f"ran {name} with {params}"


# ---------------------------------------------------------------------------
# SessionEnd lifecycle — fires exactly once at real session termination
# ---------------------------------------------------------------------------


def test_session_end_fires_exactly_once_on_shutdown(tmp_path):
    count = tmp_path / "count.txt"
    eng = _engine([_handler("SessionEnd", f"echo x >> {count}")])
    session = _session(eng)

    asyncio.run(session.submit(Shutdown()))

    assert count.read_text().count("x") == 1
    event = asyncio.run(session.next_event())
    assert isinstance(event.msg, ShutdownComplete)


def test_session_end_reason_matcher(tmp_path):
    shutdown_hits = tmp_path / "shutdown.txt"
    other_hits = tmp_path / "other.txt"
    eng = _engine(
        [
            _handler("SessionEnd", f"echo x >> {shutdown_hits}", matcher="shutdown"),
            _handler("SessionEnd", f"echo x >> {other_hits}", matcher="complete"),
        ]
    )
    session = _session(eng)

    asyncio.run(session.submit(Shutdown()))

    assert shutdown_hits.read_text().count("x") == 1
    assert not other_hits.exists()


def test_session_end_hook_failure_non_fatal():
    eng = _engine([_handler("SessionEnd", "exit 3")])
    session = _session(eng)

    # A failing SessionEnd hook must never crash the session close.
    asyncio.run(session.submit(Shutdown()))
    event = asyncio.run(session.next_event())
    assert isinstance(event.msg, ShutdownComplete)


def test_normal_turn_does_not_trigger_session_end(tmp_path):
    count = tmp_path / "count.txt"
    eng = _engine([_handler("SessionEnd", f"echo x >> {count}")])
    session = _session(eng)

    async def _noop_user_input(op):
        return None

    session._run_user_input = _noop_user_input  # type: ignore[assignment]
    asyncio.run(session.submit(UserInput("hello")))
    assert not count.exists()

    asyncio.run(session.submit(Shutdown()))
    assert count.read_text().count("x") == 1


# ---------------------------------------------------------------------------
# PreCompact checkpoint — bounded, provider-safe re-injection
# ---------------------------------------------------------------------------


def test_build_precompact_checkpoint_empty():
    from core.agent_runtime.runner import _build_precompact_checkpoint

    assert _build_precompact_checkpoint([]) is None
    assert _build_precompact_checkpoint(["   "]) is None


def test_build_precompact_checkpoint_limits():
    from core.agent_runtime.runner import (
        _PRECOMPACT_CHECKPOINT_PREFIX,
        _PRECOMPACT_CONTEXT_LIMIT,
        _PRECOMPACT_TOTAL_LIMIT,
        _build_precompact_checkpoint,
    )

    long_ctx = "y" * (_PRECOMPACT_CONTEXT_LIMIT * 2)
    checkpoint = _build_precompact_checkpoint([long_ctx])
    assert checkpoint is not None
    body = checkpoint[len(_PRECOMPACT_CHECKPOINT_PREFIX) + 1 :]
    assert len(body) == _PRECOMPACT_CONTEXT_LIMIT

    many = ["z" * 3000] * 10
    checkpoint = _build_precompact_checkpoint(many)
    assert checkpoint is not None
    body = checkpoint[len(_PRECOMPACT_CHECKPOINT_PREFIX) + 1 :]
    assert len(body) <= _PRECOMPACT_TOTAL_LIMIT


def test_maybe_compact_checkpoint_injected_after_success(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunSpec, AgentRunner

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(
        "core.agent_runtime.runner.estimate_prompt_tokens_chain",
        lambda *args, **kwargs: (999_999, None),
    )

    async def fake_summarize(spec, messages, *, response_observer=None):
        return "handoff summary"

    monkeypatch.setattr(runner, "_summarize", fake_summarize)

    async def pre_compact_hook(trigger):
        return SimpleNamespace(block=False, additional_contexts=["checkpoint ctx"])

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_FakeTools(),
        model="m",
        max_iterations=1,
        max_tool_result_chars=100000,
        context_window_tokens=8000,
        pre_compact_hook=pre_compact_hook,
    )
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3"},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    checkpoint_msgs = [
        m for m in compacted if m.get("role") == "user" and "PreCompact checkpoint" in str(m.get("content"))
    ]
    assert checkpoint_msgs, "checkpoint must survive a successful compaction"
    assert "checkpoint ctx" in checkpoint_msgs[0]["content"]


def test_maybe_compact_block_skips_checkpoint(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunSpec, AgentRunner

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(
        "core.agent_runtime.runner.estimate_prompt_tokens_chain",
        lambda *args, **kwargs: (999_999, None),
    )

    async def pre_compact_hook(trigger):
        return SimpleNamespace(block=True, additional_contexts=["should not appear"])

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_FakeTools(),
        model="m",
        max_iterations=1,
        max_tool_result_chars=100000,
        context_window_tokens=8000,
        pre_compact_hook=pre_compact_hook,
    )
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3"},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    assert compacted is messages  # compaction aborted this turn
    assert "PreCompact checkpoint" not in json.dumps(compacted)


def test_maybe_compact_summarize_failure_no_checkpoint(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunSpec, AgentRunner

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(
        "core.agent_runtime.runner.estimate_prompt_tokens_chain",
        lambda *args, **kwargs: (999_999, None),
    )

    async def fake_summarize_fails(spec, messages, *, response_observer=None):
        return None  # summarization failed

    monkeypatch.setattr(runner, "_summarize", fake_summarize_fails)

    async def pre_compact_hook(trigger):
        return SimpleNamespace(block=False, additional_contexts=["should not appear"])

    spec = AgentRunSpec(
        initial_messages=[],
        tools=_FakeTools(),
        model="m",
        max_iterations=1,
        max_tool_result_chars=100000,
        context_window_tokens=8000,
        pre_compact_hook=pre_compact_hook,
    )
    messages = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3"},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    assert compacted is messages
    assert "PreCompact checkpoint" not in json.dumps(compacted)


# ---------------------------------------------------------------------------
# deepcode-hooks MCP list-format discovery (hooks_config.json)
# ---------------------------------------------------------------------------


def _write_config(home, ws, payload):
    (home / ".deepcode").mkdir(parents=True, exist_ok=True)
    (ws / ".deepcode").mkdir(parents=True, exist_ok=True)
    (home / ".deepcode" / "hooks_config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return str(ws), str(home)


def test_mcp_list_format_accepted_from_user_mcp(tmp_path):
    ws, home = _write_config(
        tmp_path / "home",
        tmp_path / "ws",
        {"hooks": [{"name": "h1", "event": "PreToolUse", "handler": "echo hi"}]},
    )
    result = discover_hooks(ws, home)
    assert result.warnings == []
    assert any(h.event_name == "PreToolUse" and h.command == "echo hi" for h in result.handlers)


def test_mcp_list_format_rejected_from_other_sources(tmp_path):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    (home / ".deepcode").mkdir(parents=True, exist_ok=True)
    (ws / ".deepcode").mkdir(parents=True, exist_ok=True)
    # list shape in a project hooks.json (non user-mcp source) must be rejected
    (ws / ".deepcode" / "hooks.json").write_text(
        json.dumps({"hooks": [{"name": "h1", "event": "PreToolUse", "handler": "echo hi"}]}),
        encoding="utf-8",
    )
    result = discover_hooks(str(ws), str(home))
    assert any("list-shaped hooks" in w for w in result.warnings)
    assert result.handlers == []


def test_mcp_priority_ordering(tmp_path):
    ws, home = _write_config(
        tmp_path / "home",
        tmp_path / "ws",
        {
            "hooks": [
                {"name": "low", "event": "PreToolUse", "handler": "echo low", "priority": 1},
                {"name": "high", "event": "PreToolUse", "handler": "echo high", "priority": 10},
            ]
        },
    )
    result = discover_hooks(ws, home)
    pre_tool = [h for h in result.handlers if h.event_name == "PreToolUse"]
    assert [h.command for h in pre_tool] == ["echo high", "echo low"]
    assert pre_tool[0].display_order < pre_tool[1].display_order


def test_mcp_disabled_and_invalid_entries(tmp_path):
    ws, home = _write_config(
        tmp_path / "home",
        tmp_path / "ws",
        {
            "hooks": [
                {"name": "disabled", "event": "PreToolUse", "handler": "echo x", "enabled": False},
                {"name": "no-event", "handler": "echo x"},
                {"name": "bad-type", "event": "PreToolUse", "handler": "pass", "type": "python"},
                {"name": "ok", "event": "PreToolUse", "handler": "echo ok"},
            ]
        },
    )
    result = discover_hooks(ws, home)
    assert [h.command for h in result.handlers] == ["echo ok"]
    assert any("without an event" in w for w in result.warnings)
    assert any("only shell/node" in w for w in result.warnings)


def test_mcp_timeout_and_event_alias(tmp_path):
    ws, home = _write_config(
        tmp_path / "home",
        tmp_path / "ws",
        {"hooks": [{"name": "aliased", "event": "sessionStart", "handler": "echo aliased", "timeout": 7}]},
    )
    result = discover_hooks(ws, home)
    assert result.warnings == []
    hook = result.handlers[0]
    assert hook.event_name == "SessionStart"
    assert hook.timeout_sec == 7
