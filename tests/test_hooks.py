"""Tests for C3 external-command hooks — engine, discovery, exit-code protocol.

Hooks are exercised as REAL subprocesses (``sh -lc`` commands that echo JSON or
exit with a code), so we test the true execution path, not a mock of it.
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
from core.harness.hooks.events import (  # noqa: E402
    matches_matcher,
    validate_matcher,
)

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX shell required")


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


# -- matcher semantics (mirror the reference common.rs tests) --------------


def test_matcher_match_all_and_none():
    assert matches_matcher(None, "Bash")
    assert matches_matcher("*", "Bash")
    assert matches_matcher("", "Edit")


def test_matcher_exact_pipe_alternatives():
    assert matches_matcher("Edit|Write", "Edit")
    assert matches_matcher("Edit|Write", "Write")
    assert not matches_matcher("Edit|Write", "Bash")


def test_matcher_literal_is_exact_not_prefix():
    assert matches_matcher("Bash", "Bash")
    assert not matches_matcher("Bash", "BashOutput")
    assert not matches_matcher("mcp__memory", "mcp__memory__create")


def test_matcher_regex_and_anchors():
    assert matches_matcher("^Bash", "BashOutput")
    assert matches_matcher("^Bash$", "Bash")
    assert not matches_matcher("^Bash$", "BashOutput")
    assert matches_matcher("mcp__.*__write.*", "mcp__fs__write_file")


def test_invalid_regex_matches_nothing_and_is_flagged():
    assert validate_matcher("[") is not None
    assert not matches_matcher("[", "Bash")
    assert validate_matcher("^Bash$") is None
    assert validate_matcher("*") is None


# -- discovery -------------------------------------------------------------


def test_discovery_parses_and_orders(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo a"}]},
                        {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo b"}]},
                    ],
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo c", "timeout": 5}]}
                    ],
                }
            }
        )
    )
    result = discover_hooks(str(tmp_path), home=str(tmp_path / "nonexistent_home"))
    assert result.warnings == []
    assert [(h.event_name, h.matcher, h.command, h.display_order) for h in result.handlers] == [
        ("PreToolUse", "Bash", "echo a", 0),
        ("PreToolUse", "Edit", "echo b", 1),
        ("SessionStart", None, "echo c", 2),
    ]
    assert result.handlers[2].timeout_sec == 5
    assert result.handlers[0].timeout_sec == 600  # default


def test_discovery_skips_unsupported_and_warns(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "[", "hooks": [{"type": "command", "command": "echo bad"}]},
                        {"hooks": [{"type": "prompt"}]},
                        {"hooks": [{"type": "command", "command": "echo x", "async": True}]},
                        {"hooks": [{"type": "command", "command": "   "}]},
                    ]
                }
            }
        )
    )
    result = discover_hooks(str(tmp_path), home=str(tmp_path / "no_home"))
    assert result.handlers == []
    assert any("invalid matcher" in w for w in result.warnings)
    assert any("prompt" in w for w in result.warnings)
    assert any("async" in w for w in result.warnings)
    assert any("empty" in w for w in result.warnings)


def test_discovery_none_when_no_files(tmp_path):
    engine, warnings = HooksEngine.discover(str(tmp_path), "sess", home=str(tmp_path / "no_home"))
    assert engine is None
    assert warnings == []


def test_userprompt_matcher_forced_none(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "hooks.json").write_text(
        json.dumps(
            {"hooks": {"UserPromptSubmit": [{"matcher": "[", "hooks": [{"type": "command", "command": "echo u"}]}]}}
        )
    )
    result = discover_hooks(str(tmp_path), home=str(tmp_path / "no_home"))
    # UserPromptSubmit ignores matchers, so the invalid "[" is dropped, not rejected.
    assert result.warnings == []
    assert result.handlers[0].matcher is None


# -- exit-code protocol & decisions (real subprocess) ----------------------


def test_pre_tool_use_permission_deny_blocks():
    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "nope"}})
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="Bash")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {"command": "rm -rf /"}))
    assert res.block is True
    assert res.block_reason == "nope"


def test_pre_tool_use_exit_2_blocks_with_stderr():
    eng = _engine([_handler("PreToolUse", "echo danger >&2; exit 2", matcher="*")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {"command": "x"}))
    assert res.block is True
    assert res.block_reason == "danger"


def test_pre_tool_use_exit_2_empty_stderr_is_failure_not_block():
    eng = _engine([_handler("PreToolUse", "exit 2", matcher="*")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {"command": "x"}))
    assert res.block is False


def test_pre_tool_use_allow_with_updated_input():
    out = json.dumps(
        {"hookSpecificOutput": {"permissionDecision": "allow", "updatedInput": {"command": "ls"}}}
    )
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="Bash")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {"command": "rm"}))
    assert res.block is False
    assert res.updated_input == {"command": "ls"}


def test_non_json_stdout_is_noop():
    eng = _engine([_handler("PreToolUse", "echo hello world", matcher="*")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {}))
    assert res.block is False and res.additional_contexts == []


def test_matcher_selects_only_relevant_handlers():
    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}})
    eng = _engine(
        [
            _handler("PreToolUse", f"echo '{out}'", matcher="Edit", order=0),
            _handler("PreToolUse", "echo ok", matcher="Bash", order=1),
        ]
    )
    # Bash tool: only the second (non-blocking) handler matches.
    res = asyncio.run(eng.run_pre_tool_use("Bash", {}))
    assert res.block is False


def test_post_tool_use_additional_context_accumulates_in_order():
    o1 = json.dumps({"hookSpecificOutput": {"additionalContext": "first"}})
    o2 = json.dumps({"hookSpecificOutput": {"additionalContext": "second"}})
    eng = _engine(
        [
            _handler("PostToolUse", f"echo '{o1}'", matcher="*", order=0),
            _handler("PostToolUse", f"echo '{o2}'", matcher="*", order=1),
        ]
    )
    res = asyncio.run(eng.run_post_tool_use("Bash", {}, "output"))
    assert res.additional_contexts == ["first", "second"]


def test_post_tool_use_block_decision():
    out = json.dumps({"decision": "block", "reason": "bad result"})
    eng = _engine([_handler("PostToolUse", f"echo '{out}'", matcher="*")])
    res = asyncio.run(eng.run_post_tool_use("Bash", {}, "out"))
    assert res.block is True and res.block_reason == "bad result"


def test_session_start_injects_context():
    out = json.dumps({"hookSpecificOutput": {"additionalContext": "repo uses uv"}})
    eng = _engine([_handler("SessionStart", f"echo '{out}'")])
    res = asyncio.run(eng.run_session_start("startup"))
    assert res.additional_contexts == ["repo uses uv"]


def test_user_prompt_submit_block():
    out = json.dumps({"decision": "block", "reason": "off topic"})
    eng = _engine([_handler("UserPromptSubmit", f"echo '{out}'")])
    res = asyncio.run(eng.run_user_prompt_submit("hi"))
    assert res.block is True and res.block_reason == "off topic"


def test_stop_block_means_keep_going():
    out = json.dumps({"decision": "block", "reason": "not done yet"})
    eng = _engine([_handler("Stop", f"echo '{out}'")])
    res = asyncio.run(eng.run_stop())
    assert res.block is True and res.block_reason == "not done yet"


def test_payload_delivered_on_stdin(tmp_path):
    capture = tmp_path / "payload.json"
    eng = _engine([_handler("PreToolUse", f"cat > {capture}", matcher="*")])
    asyncio.run(eng.run_pre_tool_use("Bash", {"command": "ls"}, tool_use_id="tu-9"))
    payload = json.loads(capture.read_text())
    assert payload["session_id"] == "sess-1"
    assert payload["hook_event_name"] == "PreToolUse"
    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"] == {"command": "ls"}
    assert payload["tool_use_id"] == "tu-9"
    assert "model" in payload and "permission_mode" in payload  # Codex parity fields
    assert "cwd" in payload


def test_block_reason_requires_non_empty():
    # decision:block with empty reason is invalid → not a block.
    out = json.dumps({"decision": "block", "reason": "  "})
    eng = _engine([_handler("PostToolUse", f"echo '{out}'", matcher="*")])
    res = asyncio.run(eng.run_post_tool_use("Bash", {}, "out"))
    assert res.block is False


def test_first_block_reason_wins_context_accumulates():
    o1 = json.dumps({"hookSpecificOutput": {"additionalContext": "ctx"}})
    deny = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "first"}})
    deny2 = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "second"}})
    eng = _engine(
        [
            _handler("PreToolUse", f"echo '{o1}'", matcher="*", order=0),
            _handler("PreToolUse", f"echo '{deny}'", matcher="*", order=1),
            _handler("PreToolUse", f"echo '{deny2}'", matcher="*", order=2),
        ]
    )
    res = asyncio.run(eng.run_pre_tool_use("Bash", {}))
    assert res.block is True
    assert res.block_reason == "first"  # declaration order
    assert res.additional_contexts == ["ctx"]


def test_timeout_is_not_a_block():
    eng = _engine([_handler("PreToolUse", "sleep 5", matcher="*", timeout=1)])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {}))
    assert res.block is False


# -- Claude-Code-compatible plain-text context (the `echo "..."` hook) ------


def test_userprompt_plaintext_stdout_becomes_context():
    eng = _engine([_handler("UserPromptSubmit", "echo just some context")])
    res = asyncio.run(eng.run_user_prompt_submit("hi"))
    assert res.additional_contexts == ["just some context"]
    assert res.block is False


def test_sessionstart_plaintext_stdout_becomes_context():
    eng = _engine([_handler("SessionStart", "echo repo uses uv and ruff")])
    res = asyncio.run(eng.run_session_start())
    assert res.additional_contexts == ["repo uses uv and ruff"]


def test_posttooluse_plaintext_stdout_ignored():
    # Only the context events convert plain stdout; tool events ignore it.
    eng = _engine([_handler("PostToolUse", "echo noise", matcher="*")])
    res = asyncio.run(eng.run_post_tool_use("Bash", {}, "out"))
    assert res.additional_contexts == []


# -- continue:false semantics ----------------------------------------------


def test_continue_false_stops_userprompt():
    out = json.dumps({"continue": False, "stopReason": "quota exceeded"})
    eng = _engine([_handler("UserPromptSubmit", f"echo '{out}'")])
    res = asyncio.run(eng.run_user_prompt_submit("hi"))
    assert res.block is True and res.block_reason == "quota exceeded"


def test_continue_false_stops_sessionstart_with_default_reason():
    out = json.dumps({"continue": False})
    eng = _engine([_handler("SessionStart", f"echo '{out}'")])
    res = asyncio.run(eng.run_session_start())
    assert res.block is True and res.block_reason


def test_continue_false_is_unsupported_on_pretooluse():
    out = json.dumps({"continue": False, "hookSpecificOutput": {"additionalContext": "x"}})
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="*")])
    res = asyncio.run(eng.run_pre_tool_use("Bash", {}))
    # Unsupported → the handler fails; it injects neither a block nor context.
    assert res.block is False
    assert res.additional_contexts == []


def test_failed_handler_contributes_nothing_but_others_still_apply():
    ctx = json.dumps({"hookSpecificOutput": {"additionalContext": "kept"}})
    bad = json.dumps({"decision": "block", "reason": "   "})  # invalid → failed
    eng = _engine(
        [
            _handler("PostToolUse", f"echo '{bad}'", matcher="*", order=0),
            _handler("PostToolUse", f"echo '{ctx}'", matcher="*", order=1),
        ]
    )
    res = asyncio.run(eng.run_post_tool_use("Bash", {}, "out"))
    assert res.block is False  # the invalid block was dropped
    assert res.additional_contexts == ["kept"]  # the healthy handler still applied


# -- runner wiring (real _run_tool, no LLM) --------------------------------


class _FakeTools:
    """Minimal tool registry: records the params each tool ran with."""

    def __init__(self):
        self.calls = []

    async def execute(self, name, params):
        self.calls.append((name, params))
        return f"ran {name} with {params}"


def _spec(tools, **kw):
    from core.agent_runtime.runner import AgentRunSpec

    return AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="m",
        max_iterations=1,
        max_tool_result_chars=100000,
        **kw,
    )


def _call(runner, spec, name, arguments):
    from core.providers.base import ToolCallRequest

    return asyncio.run(
        runner._run_tool(spec, ToolCallRequest(id="1", name=name, arguments=arguments), {})
    )


def test_runner_pre_tool_use_blocks_tool_from_running():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "forbidden"}})
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="*")])
    tools = _FakeTools()
    spec = _spec(tools, pre_tool_hook=eng.run_pre_tool_use)
    result, event, error = _call(AgentRunner(None), spec, "Bash", {"command": "rm -rf /"})
    assert "blocked by PreToolUse hook: forbidden" in result
    assert event["status"] == "denied"
    assert tools.calls == []  # the tool never executed


def test_runner_pre_tool_use_rewrites_arguments():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps({"hookSpecificOutput": {"permissionDecision": "allow", "updatedInput": {"command": "ls -la"}}})
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="*")])
    tools = _FakeTools()
    spec = _spec(tools, pre_tool_hook=eng.run_pre_tool_use)
    result, _event, _error = _call(AgentRunner(None), spec, "Bash", {"command": "rm"})
    assert tools.calls == [("Bash", {"command": "ls -la"})]  # rewritten input reached the tool


def test_runner_post_tool_use_injects_context_into_result():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps({"hookSpecificOutput": {"additionalContext": "remember to run tests"}})
    eng = _engine([_handler("PostToolUse", f"echo '{out}'", matcher="*")])
    tools = _FakeTools()
    spec = _spec(tools, post_tool_hook=eng.run_post_tool_use)
    result, event, _error = _call(AgentRunner(None), spec, "Bash", {"command": "ls"})
    assert event["status"] == "ok"
    assert "ran Bash" in result
    assert "[hook] remember to run tests" in result


def test_runner_no_hooks_is_unchanged():
    from core.agent_runtime.runner import AgentRunner

    tools = _FakeTools()
    spec = _spec(tools)  # no hooks
    result, event, error = _call(AgentRunner(None), spec, "Bash", {"command": "ls"})
    assert result == "ran Bash with {'command': 'ls'}"
    assert event["status"] == "ok" and error is None


# -- session wiring (SessionStart / UserPromptSubmit) ----------------------


def _session(hooks_engine):
    from core.events.session import AgentSession

    return AgentSession(
        provider=None,
        tools=_FakeTools(),
        model="m",
        hooks_engine=hooks_engine,
        context_window_tokens=8000,
    )


def test_session_start_and_prompt_context_injected():
    s_out = json.dumps({"hookSpecificOutput": {"additionalContext": "SESSION CTX"}})
    p_out = json.dumps({"hookSpecificOutput": {"additionalContext": "PROMPT CTX"}})
    eng = _engine(
        [
            _handler("SessionStart", f"echo '{s_out}'"),
            _handler("UserPromptSubmit", f"echo '{p_out}'"),
        ]
    )
    session = _session(eng)
    contexts: list[str] = []
    blocked = asyncio.run(session._run_prompt_hooks("hello", contexts))
    assert blocked is None
    assert contexts == ["SESSION CTX", "PROMPT CTX"]
    # SessionStart fires only once.
    contexts2: list[str] = []
    asyncio.run(session._run_prompt_hooks("again", contexts2))
    assert contexts2 == ["PROMPT CTX"]


def test_user_prompt_submit_blocks_turn():
    out = json.dumps({"decision": "block", "reason": "not allowed"})
    eng = _engine([_handler("UserPromptSubmit", f"echo '{out}'")])
    session = _session(eng)
    blocked = asyncio.run(session._run_prompt_hooks("bad", []))
    assert blocked == "not allowed"


def test_blocked_prompt_ends_turn_without_calling_the_model():
    # A UserPromptSubmit block must end the turn with task_complete and never
    # reach the runner/provider — the whole point of a blocking prompt hook.
    from core.events.session import AgentSession

    out = json.dumps({"decision": "block", "reason": "policy violation"})
    eng = _engine([_handler("UserPromptSubmit", f"echo '{out}'")])
    session = AgentSession(
        provider=object(),  # would explode if the runner tried to use it
        tools=_FakeTools(),
        model="m",
        hooks_engine=eng,
        context_window_tokens=8000,
    )
    asyncio.run(session._run_user_input("do something"))
    events = session.drain_events()
    completes = [e for e in events if e.msg.type == "task_complete"]
    assert len(completes) == 1
    assert completes[0].msg.stop_reason == "blocked_by_hook"
    assert completes[0].msg.final_text == "policy violation"
    # No model turn happened: history holds only the user message.
    assert [m["role"] for m in session.history] == ["user"]
    assert session._busy is False
