"""Tests for the strictness-only risk evaluator in the permission engine (#245).

Action triage, Option A: the evaluator runs only on a call the mode default
would allow, and its one move is ``allow`` -> ``ask``. It can never loosen a
``deny``, override an explicit rule, or touch the sensitive-path / read-only
boundaries. It is default off and advisory, not a security boundary.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.domain.execution_security import ApprovalPolicy, ExecutionSecurityProfile
from core.harness.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
    make_engine,
)
from core.harness.policy import build_permission_engine
from core.harness.risk_evaluator import (
    CommandGuardRiskEvaluator,
    default_risk_evaluator,
)
from core.providers.base import LLMResponse, ToolCallRequest

ALLOW = PermissionDecision.ALLOW
ASK = PermissionDecision.ASK
DENY = PermissionDecision.DENY


def _decide(engine: PermissionEngine, tool: str, **args):
    decision, _reason = engine.evaluate(tool, args)
    return decision


class _Spy:
    """Flags every call with a fixed opinion and counts how often it is asked."""

    def __init__(self, opinion: object = "flagged") -> None:
        self.opinion = opinion
        self.calls = 0

    def __call__(self, tool_name: str, arguments: Mapping[str, object]):
        self.calls += 1
        if isinstance(self.opinion, BaseException):
            raise self.opinion
        return self.opinion


# ---- 1. the evaluator cannot loosen anything --------------------------------


def test_sensitive_path_and_plan_deny_survive_an_allow_opinion():
    no_objection = _Spy(opinion=None)

    full_auto = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/home/u/proj",
        risk_evaluator=no_objection,
    )
    assert _decide(full_auto, "read_file", file_path="/home/u/.ssh/id_rsa") is DENY

    plan = PermissionEngine(
        mode=PermissionMode.PLAN, cwd="/w", risk_evaluator=no_objection
    )
    assert _decide(plan, "write_file", file_path="/w/a.py") is DENY


def test_explicit_rule_stays_final_in_either_direction():
    spy = _Spy()
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        rules=[
            PermissionRule("execute_bash", "rm -rf *", ALLOW),
            PermissionRule("execute_bash", "git push *", DENY),
        ],
        cwd="/w",
        risk_evaluator=spy,
    )

    assert _decide(engine, "execute_bash", command="rm -rf build") is ALLOW
    assert _decide(engine, "execute_bash", command="git push origin main") is DENY
    assert spy.calls == 0


# ---- 2. the intended path ---------------------------------------------------


def test_full_auto_high_risk_call_asks():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=default_risk_evaluator(),
    )

    decision, reason = engine.evaluate("execute_bash", {"command": "rm -rf build"})

    assert decision is ASK
    assert reason == "risk evaluator flagged this call: recursive force remove (rm -rf)"
    assert _decide(engine, "execute_bash", command="pytest -q") is ALLOW


def test_headless_never_policy_denies_with_the_evaluator_reason():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        approval_policy=ApprovalPolicy.NEVER,
        risk_evaluator=default_risk_evaluator(),
    )

    decision, reason = engine.evaluate("execute_bash", {"command": "sudo reboot"})

    assert decision is DENY
    assert "privilege escalation (sudo)" in reason
    assert "approval policy is never" in reason


# ---- 3. evaluator failure is contained and fails closed ----------------------


def test_raising_or_garbage_evaluator_fails_closed_without_crashing():
    for broken in (_Spy(opinion=RuntimeError("boom")), _Spy(opinion=42), _Spy("")):
        engine = PermissionEngine(
            mode=PermissionMode.FULL_AUTO, cwd="/w", risk_evaluator=broken
        )

        decision, reason = engine.evaluate("execute_bash", {"command": "ls"})

        assert decision is ASK, broken.opinion
        assert "fail-closed" in reason


def test_a_transient_failure_does_not_stick():
    spy = _Spy(opinion=RuntimeError("transient"))
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO, cwd="/w", risk_evaluator=spy
    )

    engine.evaluate("execute_bash", {"command": "ls"})
    spy.opinion = None
    decision, _ = engine.evaluate("execute_bash", {"command": "ls"})

    assert decision is ALLOW
    assert spy.calls == 2


# ---- 4. no wasted calls -----------------------------------------------------


def test_not_consulted_when_steps_one_to_three_or_mode_already_decided():
    spy = _Spy()
    sensitive = PermissionEngine(
        mode=PermissionMode.FULL_AUTO, cwd="/home/u/proj", risk_evaluator=spy
    )
    sensitive.evaluate("read_file", {"file_path": "/home/u/.aws/credentials"})

    read_only = PermissionEngine(
        mode=PermissionMode.PLAN,
        cwd="/w",
        enforce_read_only=True,
        risk_evaluator=spy,
    )
    read_only.evaluate("write_file", {"file_path": "/w/a.py"})

    default_ask = PermissionEngine(
        mode=PermissionMode.DEFAULT, cwd="/w", risk_evaluator=spy
    )
    default_ask.evaluate("execute_bash", {"command": "rm -rf build"})

    assert spy.calls == 0


def test_each_call_is_judged_on_its_own_arguments():
    # No opinion cache: two calls that share a path (or any other key the
    # engine uses for rule matching) must not share a verdict.
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=CommandGuardRiskEvaluator(),
    )

    assert _decide(engine, "mcp__ops__run", command="ls", path="/w") is ALLOW
    assert _decide(engine, "mcp__ops__run", command="rm -rf /", path="/w") is ASK
    assert _decide(engine, "execute_bash", command="", cmd="ls") is ALLOW
    assert _decide(engine, "execute_bash", command="", cmd="rm -rf /") is ASK
    assert not hasattr(engine, "_risk_cache")


# ---- 5. evasion fixtures (advisory, not a security boundary) ----------------


def test_tokenised_evasions_are_flagged():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=CommandGuardRiskEvaluator(),
    )

    for command in (
        "rm  -rf  /tmp/x",
        "rm -r -f /tmp/x",
        "rm --recursive --force /tmp/x",
        "echo ok && rm -rf /tmp/x",
        "chmod 0777 /srv",
    ):
        assert _decide(engine, "execute_bash", command=command) is ASK, command


def test_known_gaps_are_documented_not_guarded():
    # Advisory only: an encoded payload is not decoded here. The sandbox, not
    # this evaluator, is the boundary — this test pins that honest limit.
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=CommandGuardRiskEvaluator(),
    )

    encoded = "echo cm0gLXJmIC8= | base64 -d > run.sh"
    assert _decide(engine, "execute_bash", command=encoded) is ALLOW
    assert _decide(engine, "execute_bash", command="touch rm-rf-notes.txt") is ALLOW


# ---- 6. feature off: identical to today -------------------------------------


def test_feature_off_decisions_and_reasons_are_identical(monkeypatch):
    # make_engine never reads the flag: even exported, nothing changes.
    monkeypatch.setenv("DEEPCODE_RISK_EVALUATOR", "1")
    calls = [
        ("read_file", {"file_path": "/home/u/proj/a.py"}),
        ("write_file", {"file_path": "/home/u/proj/a.py"}),
        ("execute_bash", {"command": "rm -rf /"}),
        ("execute_bash", {"command": "pytest -q"}),
        ("read_file", {"file_path": "/home/u/.ssh/id_rsa"}),
    ]

    for mode in ("default", "plan", "full_auto"):
        for policy in (ApprovalPolicy.ON_REQUEST, ApprovalPolicy.NEVER):
            built = make_engine(mode, cwd="/home/u/proj", approval_policy=policy)
            plain = PermissionEngine(
                mode=PermissionMode(mode),
                cwd="/home/u/proj",
                approval_policy=policy,
            )

            assert built.risk_evaluator is None
            for tool, args in calls:
                assert built.evaluate(tool, args) == plain.evaluate(tool, args)


def test_make_engine_takes_only_an_explicit_evaluator(monkeypatch):
    monkeypatch.delenv("DEEPCODE_RISK_EVALUATOR", raising=False)

    engine = make_engine(
        "full_auto", cwd="/w", risk_evaluator=CommandGuardRiskEvaluator()
    )

    assert isinstance(engine.risk_evaluator, CommandGuardRiskEvaluator)
    assert _decide(engine, "execute_bash", command="rm -rf /") is ASK


def test_runtime_engine_builder_follows_the_env_flag(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("DEEPCODE_RISK_EVALUATOR", raising=False)
    assert build_permission_engine(None, cwd="/w").risk_evaluator is None

    monkeypatch.setenv("DEEPCODE_RISK_EVALUATOR", "1")
    engine = build_permission_engine(None, cwd="/w")

    assert isinstance(engine.risk_evaluator, CommandGuardRiskEvaluator)
    assert _decide(engine, "execute_bash", command="rm -rf /") is ASK


def test_runtime_engine_builder_off_is_identical_to_main(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("DEEPCODE_RISK_EVALUATOR", raising=False)
    calls = [
        ("read_file", {"file_path": "/w/a.py"}),
        ("write_file", {"file_path": "/w/a.py"}),
        ("execute_bash", {"command": "rm -rf /"}),
        ("mcp__ops__run", {"command": "rm -rf /"}),
    ]

    for preset in ("ask", "read_only", "full_access"):
        profile = ExecutionSecurityProfile.for_preset(preset)
        off = build_permission_engine(
            None, cwd="/w", execution_security_profile=profile
        )
        assert off.risk_evaluator is None
        plain = PermissionEngine(
            mode=off.mode,
            rules=off.rules,
            cwd=off.cwd,
            approval_policy=off.approval_policy,
            protect_sensitive_paths=off.protect_sensitive_paths,
            enforce_read_only=off.enforce_read_only,
            bypass_origin_approval=off.bypass_origin_approval,
            protect_hook_configs=off.protect_hook_configs,
        )
        for tool, args in calls:
            assert off.evaluate(tool, args) == plain.evaluate(tool, args)


def test_full_access_skips_the_evaluator(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("DEEPCODE_RISK_EVALUATOR", "1")

    engine = build_permission_engine(
        None,
        cwd="/w",
        execution_security_profile=ExecutionSecurityProfile.for_preset("full_access"),
    )

    # Full Access approves nothing (policy ``never``), so an ask would be an
    # unapprovable deny. It skips the evaluator as it skips origin approval.
    assert engine.risk_evaluator is None
    assert engine.evaluate("mcp__ops__run", {"command": "rm -rf /tmp/x"}) == (
        ALLOW,
        "full_auto mode: no implicit gate",
    )


def test_read_only_preset_turns_a_flag_into_a_deny(monkeypatch):
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("DEEPCODE_RISK_EVALUATOR", "1")

    engine = build_permission_engine(
        None,
        cwd="/w",
        execution_security_profile=ExecutionSecurityProfile.for_preset("read_only"),
    )

    decision, reason = engine.evaluate_tool(
        "mcp__ops__inspect", {"command": "rm -rf /tmp/x"}, read_only=True
    )
    assert decision is DENY
    assert "risk evaluator flagged this call" in reason
    assert "approval policy is never" in reason


# ---- 7. origin gate and runner paths ----------------------------------------


def test_mcp_origin_gate_composes_with_the_evaluator():
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=CommandGuardRiskEvaluator(),
    )

    # An ``auto`` origin cannot waive the evaluator's ask...
    decision, reason = engine.evaluate_tool(
        "mcp__ops__run", {"command": "rm -rf /tmp/x"}, approval_mode="auto"
    )
    assert decision is ASK
    assert reason.startswith("risk evaluator flagged this call")

    # ...an unflagged call still meets the origin's own gate...
    decision, reason = engine.evaluate_tool(
        "mcp__ops__run", {"command": "ls"}, approval_mode="prompt"
    )
    assert (decision, reason) == (ASK, "MCP tool policy requires confirmation")

    # ...and an unflagged call under ``auto`` stays allowed.
    decision, _ = engine.evaluate_tool(
        "mcp__ops__run", {"command": "ls"}, approval_mode="auto"
    )
    assert decision is ALLOW


@tool_parameters(
    {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
)
class _RecordingRunTool(Tool):
    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "mcp__ops__run"

    @property
    def description(self) -> str:
        return "Run a command on the ops server."

    async def execute(self, **kwargs: Any) -> Any:
        self.ran.append(kwargs["command"])
        return f"ran: {kwargs['command']}"


class _ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
async def test_runner_without_approver_blocks_a_flagged_call():
    tool = _RecordingRunTool()
    tools = ToolRegistry()
    tools.register(tool)
    provider = _ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="c1", name="mcp__ops__run", arguments={"command": "ls"}
                    ),
                    ToolCallRequest(
                        id="c2",
                        name="mcp__ops__run",
                        arguments={"command": "rm -rf /tmp/x"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        cwd="/w",
        risk_evaluator=CommandGuardRiskEvaluator(),
    )

    result = await AgentRunner(provider).run(
        AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "test"},
                {"role": "user", "content": "go"},
            ],
            tools=tools,
            model="fake-model",
            max_iterations=5,
            max_tool_result_chars=10_000,
            permission_checker=engine.evaluate,
            approval_callback=None,
        )
    )

    assert tool.ran == ["ls"]
    blocked = next(
        m["content"]
        for m in result.messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "c2"
    )
    assert "risk evaluator flagged this call" in blocked
    assert "no approver attached" in blocked
