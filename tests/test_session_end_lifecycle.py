"""SessionEnd lifecycle and PreCompact checkpoint end-to-end tests.

Covers the lifecycle contracts introduced by the SessionEnd / PreCompact work:

- ``SessionEnd`` fires exactly once at real session termination
  (``AgentSession.submit(Shutdown)``), never per turn; the session-exit reason
  doubles as the matcher input (DeepCode shutdown maps to ``other``); a failing
  hook is non-fatal and never blocks ``ShutdownComplete``.
- The ``PreCompact`` hook's ``additional_contexts`` survive a successful
  compaction as a single bounded, provider-agnostic user message, and are
  absent when the hook blocks or summarization fails.

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

from core.events.protocol import Shutdown, ShutdownComplete, UserInput
from core.harness.hooks.discovery import Handler
from core.harness.hooks.engine import HooksEngine

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

    async def aclose(self):
        return None


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

    asyncio.run(session.submit(Shutdown()))
    asyncio.run(session.aclose())
    assert count.read_text().count("x") == 1


def test_session_end_fires_from_real_close_path(tmp_path):
    count = tmp_path / "count.txt"
    eng = _engine([_handler("SessionEnd", f"echo x >> {count}")])
    session = _session(eng)

    asyncio.run(session.aclose())
    asyncio.run(session.aclose())

    assert count.read_text().count("x") == 1


def test_session_end_reason_matcher(tmp_path):
    shutdown_hits = tmp_path / "shutdown.txt"
    other_hits = tmp_path / "other.txt"
    eng = _engine(
        [
            _handler("SessionEnd", f"echo x >> {shutdown_hits}", matcher="other"),
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


def test_session_end_is_not_emitted_for_subagent_session(tmp_path):
    count = tmp_path / "count.txt"
    eng = _engine([_handler("SessionEnd", f"echo x >> {count}")])
    session = _session(eng)
    session._agent_context = ("child-1", "subagent")

    asyncio.run(session.aclose())

    assert not count.exists()


def test_session_end_discovery_uses_bounded_exit_timeout(tmp_path):
    from core.harness.hooks.discovery import discover_hooks

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    (home / ".deepcode").mkdir(parents=True)
    workspace.mkdir()
    (home / ".deepcode" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {"matcher": "*", "hooks": [{"command": "echo default"}]},
                        {
                            "matcher": "*",
                            "hooks": [{"command": "echo capped", "timeout": 999}],
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = discover_hooks(str(workspace), str(home))

    assert [handler.timeout_sec for handler in result.handlers] == [2, 60]


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
    assert len(checkpoint) <= _PRECOMPACT_TOTAL_LIMIT


def test_build_precompact_checkpoint_honors_smaller_dynamic_limit():
    from core.agent_runtime.runner import _build_precompact_checkpoint

    checkpoint = _build_precompact_checkpoint(["x" * 500], total_limit=100)
    assert checkpoint is not None
    assert len(checkpoint) <= 100


def test_maybe_compact_checkpoint_injected_after_success(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunner, AgentRunSpec

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(runner, "_estimate_prompt", lambda spec, messages: 999_999)

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
        {"role": "user", "content": "turn 1 " + "context " * 50},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2 " + "context " * 50},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3 " + "context " * 50},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    checkpoint_msgs = [
        m
        for m in compacted
        if m.get("role") == "user" and "PreCompact checkpoint" in str(m.get("content"))
    ]
    assert checkpoint_msgs, "checkpoint must survive a successful compaction"
    assert "checkpoint ctx" in checkpoint_msgs[0]["content"]
    assert sum(len(str(m.get("content", ""))) for m in compacted) < sum(
        len(str(m.get("content", ""))) for m in messages
    )


def test_maybe_compact_block_skips_checkpoint(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunner, AgentRunSpec

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(runner, "_estimate_prompt", lambda spec, messages: 999_999)

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
        {"role": "user", "content": "turn 1 " + "context " * 50},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2 " + "context " * 50},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3 " + "context " * 50},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    assert compacted is messages  # compaction aborted this turn
    assert "PreCompact checkpoint" not in json.dumps(compacted)


def test_maybe_compact_summarize_failure_no_checkpoint(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime.runner import AgentRunner, AgentRunSpec

    runner = AgentRunner(provider=object())
    monkeypatch.setattr(runner, "_estimate_prompt", lambda spec, messages: 999_999)

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
        {"role": "user", "content": "turn 1 " + "context " * 50},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "turn 2 " + "context " * 50},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "turn 3 " + "context " * 50},
    ]
    compacted = asyncio.run(runner._maybe_compact(spec, messages))
    assert compacted is messages
    assert "PreCompact checkpoint" not in json.dumps(compacted)
