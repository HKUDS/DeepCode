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
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo a"}],
                        },
                        {
                            "matcher": "Edit",
                            "hooks": [{"type": "command", "command": "echo b"}],
                        },
                    ],
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "echo c", "timeout": 5}
                            ]
                        }
                    ],
                }
            }
        )
    )
    result = discover_hooks(str(tmp_path), home=str(tmp_path / "nonexistent_home"))
    assert result.warnings == []
    assert [
        (h.event_name, h.matcher, h.command, h.display_order) for h in result.handlers
    ] == [
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
                        {
                            "matcher": "[",
                            "hooks": [{"type": "command", "command": "echo bad"}],
                        },
                        {"hooks": [{"type": "prompt"}]},
                        {
                            "hooks": [
                                {"type": "command", "command": "echo x", "async": True}
                            ]
                        },
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
    engine, warnings = HooksEngine.discover(
        str(tmp_path), "sess", home=str(tmp_path / "no_home")
    )
    assert engine is None
    assert warnings == []


def test_userprompt_matcher_forced_none(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "[",
                            "hooks": [{"type": "command", "command": "echo u"}],
                        }
                    ]
                }
            }
        )
    )
    result = discover_hooks(str(tmp_path), home=str(tmp_path / "no_home"))
    # UserPromptSubmit ignores matchers, so the invalid "[" is dropped, not rejected.
    assert result.warnings == []
    assert result.handlers[0].matcher is None


# -- exit-code protocol & decisions (real subprocess) ----------------------


def test_pre_tool_use_permission_deny_blocks():
    out = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "nope",
            }
        }
    )
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
        {
            "hookSpecificOutput": {
                "permissionDecision": "allow",
                "updatedInput": {"command": "ls"},
            }
        }
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
    out = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "no",
            }
        }
    )
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
    deny = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "first",
            }
        }
    )
    deny2 = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "second",
            }
        }
    )
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
    out = json.dumps(
        {"continue": False, "hookSpecificOutput": {"additionalContext": "x"}}
    )
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
        runner._run_tool(
            spec, ToolCallRequest(id="1", name=name, arguments=arguments), {}
        )
    )


def test_runner_pre_tool_use_blocks_tool_from_running():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "forbidden",
            }
        }
    )
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="*")])
    tools = _FakeTools()
    spec = _spec(tools, pre_tool_hook=eng.run_pre_tool_use)
    result, event, error = _call(
        AgentRunner(None), spec, "Bash", {"command": "rm -rf /"}
    )
    assert "blocked by PreToolUse hook: forbidden" in result
    assert event["status"] == "denied"
    assert tools.calls == []  # the tool never executed


def test_runner_pre_tool_use_rewrites_arguments():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps(
        {
            "hookSpecificOutput": {
                "permissionDecision": "allow",
                "updatedInput": {"command": "ls -la"},
            }
        }
    )
    eng = _engine([_handler("PreToolUse", f"echo '{out}'", matcher="*")])
    tools = _FakeTools()
    spec = _spec(tools, pre_tool_hook=eng.run_pre_tool_use)
    result, _event, _error = _call(AgentRunner(None), spec, "Bash", {"command": "rm"})
    assert tools.calls == [
        ("Bash", {"command": "ls -la"})
    ]  # rewritten input reached the tool


def test_runner_post_tool_use_injects_context_into_result():
    from core.agent_runtime.runner import AgentRunner

    out = json.dumps(
        {"hookSpecificOutput": {"additionalContext": "remember to run tests"}}
    )
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


def _session(hooks_engine, agent_context=None):
    from core.events.session import AgentSession

    return AgentSession(
        provider=None,
        tools=_FakeTools(),
        model="m",
        hooks_engine=hooks_engine,
        agent_context=agent_context,
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


# -- SubagentStart / SubagentStop (#86) ------------------------------------


def test_subagent_start_payload_and_plaintext_context(tmp_path):
    capture = tmp_path / "p.json"
    eng = _engine([_handler("SubagentStart", f"cat > {capture}; echo sub-context")])
    res = asyncio.run(eng.run_subagent_start("worker-7", "subagent"))
    assert res.additional_contexts == ["sub-context"]  # plain-text context works
    p = json.loads(capture.read_text())
    assert p["hook_event_name"] == "SubagentStart"
    assert p["agent_id"] == "worker-7" and p["agent_type"] == "subagent"


def test_subagent_stop_block_keeps_it_going():
    out = json.dumps({"decision": "block", "reason": "keep working"})
    eng = _engine([_handler("SubagentStop", f"echo '{out}'")])
    res = asyncio.run(eng.run_subagent_stop("w1"))
    assert res.block is True and res.block_reason == "keep working"


def test_subagent_session_fires_subagent_start_not_session_start():
    s = json.dumps({"hookSpecificOutput": {"additionalContext": "SESSION"}})
    sub = json.dumps({"hookSpecificOutput": {"additionalContext": "SUBAGENT"}})
    eng = _engine(
        [
            _handler("SessionStart", f"echo '{s}'"),
            _handler("SubagentStart", f"echo '{sub}'"),
        ]
    )
    session = _session(eng, agent_context=("w1", "subagent"))
    contexts: list[str] = []
    asyncio.run(session._run_prompt_hooks("go", contexts))
    # A sub-agent session uses SubagentStart, never the main SessionStart.
    assert contexts == ["SUBAGENT"]


def test_main_session_still_fires_session_start_not_subagent():
    s = json.dumps({"hookSpecificOutput": {"additionalContext": "SESSION"}})
    sub = json.dumps({"hookSpecificOutput": {"additionalContext": "SUBAGENT"}})
    eng = _engine(
        [
            _handler("SessionStart", f"echo '{s}'"),
            _handler("SubagentStart", f"echo '{sub}'"),
        ]
    )
    session = _session(eng)  # no agent_context → main session
    contexts: list[str] = []
    asyncio.run(session._run_prompt_hooks("go", contexts))
    assert contexts == ["SESSION"]


def test_user_prompt_submit_blocks_turn():
    out = json.dumps({"decision": "block", "reason": "not allowed"})
    eng = _engine([_handler("UserPromptSubmit", f"echo '{out}'")])
    session = _session(eng)
    blocked = asyncio.run(session._run_prompt_hooks("bad", []))
    assert blocked == "not allowed"


# -- PermissionRequest (#88) -----------------------------------------------


def test_permission_request_deny_wins_over_allow():
    deny = json.dumps(
        {
            "hookSpecificOutput": {
                "decision": {"behavior": "deny", "message": "policy X"}
            }
        }
    )
    allow = json.dumps({"hookSpecificOutput": {"decision": {"behavior": "allow"}}})
    eng = _engine(
        [
            _handler("PermissionRequest", f"echo '{allow}'", matcher="*", order=0),
            _handler("PermissionRequest", f"echo '{deny}'", matcher="*", order=1),
        ]
    )
    res = asyncio.run(eng.run_permission_request("bash", {"command": "x"}))
    assert res.decision == "deny" and res.message == "policy X"


def test_permission_request_allow_and_no_verdict():
    allow = json.dumps({"hookSpecificOutput": {"decision": {"behavior": "allow"}}})
    eng = _engine([_handler("PermissionRequest", f"echo '{allow}'", matcher="*")])
    assert asyncio.run(eng.run_permission_request("bash", {})).decision == "allow"
    eng2 = _engine([_handler("PermissionRequest", "echo '{}'", matcher="*")])
    assert asyncio.run(eng2.run_permission_request("bash", {})).decision is None


def test_permission_request_exit2_denies_with_stderr():
    eng = _engine(
        [
            _handler(
                "PermissionRequest", "echo blocked-by-policy >&2; exit 2", matcher="*"
            )
        ]
    )
    res = asyncio.run(eng.run_permission_request("bash", {}))
    assert res.decision == "deny" and res.message == "blocked-by-policy"


class _Scripted:
    """Provider returning a fixed sequence of responses (repeats the last)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


def _recording_registry(tool_name, sink):
    """A real ToolRegistry with one tool that records the params it ran with."""
    from core.agent_runtime.tools.base import Tool, tool_parameters
    from core.agent_runtime.tools.registry import ToolRegistry

    @tool_parameters(
        {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": [],
        }
    )
    class _Rec(Tool):
        @property
        def name(self):
            return tool_name

        @property
        def description(self):
            return "recording tool"

        async def execute(self, **kwargs):
            sink.append((tool_name, kwargs))
            return f"ran {tool_name}"

    reg = ToolRegistry()
    reg.register(_Rec())
    return reg


def _run_spec(**overrides):
    from core.agent_runtime.runner import AgentRunSpec
    from core.agent_runtime.tools.registry import ToolRegistry

    base = {
        "initial_messages": [{"role": "user", "content": "go"}],
        "tools": ToolRegistry(),
        "model": "fake-model",
        "max_iterations": 6,
        "max_tool_result_chars": 100000,
    }
    base.update(overrides)
    return AgentRunSpec(**base)


def test_runner_permission_request_hook_denies_an_ask():
    from core.agent_runtime.runner import AgentRunner
    from core.providers.base import LLMResponse, ToolCallRequest

    deny = json.dumps(
        {
            "hookSpecificOutput": {
                "decision": {"behavior": "deny", "message": "hook says no"}
            }
        }
    )
    eng = _engine([_handler("PermissionRequest", f"echo '{deny}'", matcher="*")])
    provider = _Scripted(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="bash", arguments={"command": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="ok, backing off", finish_reason="stop"),
        ]
    )

    async def ask(name, args):
        return ("ask", "confirm?")

    sink = []
    spec = _run_spec(
        tools=_recording_registry("bash", sink),
        permission_checker=ask,
        permission_request_hook=eng.run_permission_request,
    )
    result = asyncio.run(AgentRunner(provider).run(spec))
    tool_msgs = [str(m["content"]) for m in result.messages if m.get("role") == "tool"]
    assert any("hook says no" in c for c in tool_msgs)
    assert sink == []  # denied → the tool never ran


def test_runner_permission_request_hook_allows_an_ask():
    from core.agent_runtime.runner import AgentRunner
    from core.providers.base import LLMResponse, ToolCallRequest

    allow = json.dumps({"hookSpecificOutput": {"decision": {"behavior": "allow"}}})
    eng = _engine([_handler("PermissionRequest", f"echo '{allow}'", matcher="*")])
    provider = _Scripted(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(id="c1", name="bash", arguments={"command": "ls"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )

    async def ask(name, args):
        return ("ask", "confirm?")

    sink = []
    spec = _run_spec(
        tools=_recording_registry("bash", sink),
        permission_checker=ask,
        permission_request_hook=eng.run_permission_request,
    )
    asyncio.run(AgentRunner(provider).run(spec))
    assert sink == [("bash", {"command": "ls"})]  # allowed → the tool ran


# -- PreCompact / PostCompact (#89 / C4a) ----------------------------------


def test_pre_compact_hook_block_skips_and_payload(tmp_path):
    capture = tmp_path / "p.json"
    out = json.dumps({"continue": False})
    eng = _engine([_handler("PreCompact", f"cat > {capture}; echo '{out}'")])
    res = asyncio.run(eng.run_pre_compact("auto"))
    assert res.block is True  # continue:false → skip compaction
    p = json.loads(capture.read_text())
    assert p["hook_event_name"] == "PreCompact" and p["trigger"] == "auto"


def test_pre_compact_matcher_matches_trigger():
    out = json.dumps({"continue": False})
    eng = _engine([_handler("PreCompact", f"echo '{out}'", matcher="manual")])
    assert asyncio.run(eng.run_pre_compact("manual")).block is True
    assert asyncio.run(eng.run_pre_compact("auto")).block is False  # matcher misses


def test_post_compact_hook_fires_with_trigger(tmp_path):
    capture = tmp_path / "p.json"
    eng = _engine([_handler("PostCompact", f"cat > {capture}")])
    asyncio.run(eng.run_post_compact("auto"))
    p = json.loads(capture.read_text())
    assert p["hook_event_name"] == "PostCompact" and p["trigger"] == "auto"


# -- Stop hook wired into the loop (#87) -----------------------------------


def test_stop_payload_carries_stop_hook_active(tmp_path):
    capture = tmp_path / "p.json"
    eng = _engine([_handler("Stop", f"cat > {capture}")])
    asyncio.run(eng.run_stop(stop_hook_active=True))
    p = json.loads(capture.read_text())
    assert p["hook_event_name"] == "Stop" and p["stop_hook_active"] is True


def test_runner_stop_hook_forces_continuation_then_stands_down():
    from core.agent_runtime.runner import AgentRunner
    from core.harness.hooks.engine import StopOutcome
    from core.providers.base import LLMResponse

    provider = _Scripted([LLMResponse(content="done", finish_reason="stop")])
    fired = {"n": 0}

    async def stop_hook(stop_hook_active):
        fired["n"] += 1
        if stop_hook_active:
            return StopOutcome(block=False)
        return StopOutcome(block=True, block_reason="you forgot to write tests")

    result = asyncio.run(AgentRunner(provider).run(_run_spec(stop_hook=stop_hook)))
    assert provider.calls == 2  # continued exactly once
    assert result.final_content == "done"
    assert any(
        m.get("role") == "user" and "forgot to write tests" in str(m["content"])
        for m in result.messages
    )
    assert fired["n"] == 2  # blocked once, then stood down (stop_hook_active)


def test_runner_no_stop_hook_ends_normally():
    from core.agent_runtime.runner import AgentRunner
    from core.providers.base import LLMResponse

    provider = _Scripted([LLMResponse(content="done", finish_reason="stop")])
    result = asyncio.run(AgentRunner(provider).run(_run_spec()))
    assert provider.calls == 1 and result.final_content == "done"


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
