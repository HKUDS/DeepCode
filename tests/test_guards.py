"""Tests for the P3.5 loop guards — REASONIX 反漫游守卫的 DEEPCODE 落地.

Covers the four guard components ported from REASONIX §5/§6
(``scripts/REASONIX_第五阶段深度分析报告.md`` / ``REASONIX_第六阶段深度分析报告.md``):

- :class:`~core.loop.guards.EvidenceLedger` — per-round evidence scoring (0-3)
- :class:`~core.loop.guards.ProgressGuard` — consecutive zero-progress rounds,
  escalating at thresholds 2/4/6 (nudge → strategy change → force answer + block)
- :class:`~core.loop.guards.StormBreaker` — tool-sequence signature circuit breaker
- :func:`~core.loop.guards.delegation_admission` — spawn_agent admission gate
- :class:`~core.loop.guards.LoopGuards` — the runner-facing integration entry
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.guards import (  # noqa: E402
    SCORE_NEW_EVIDENCE,
    SCORE_PARTIAL_EVIDENCE,
    SCORE_REPEATED_CALLS,
    SCORE_ZERO_EVIDENCE,
    ContextualToolGate,
    DeliveryPolicyGates,
    EvidenceLedger,
    LoopGuards,
    MutationDependencyBarrier,
    ProgressGuard,
    RecoveryGate,
    StormBreaker,
    ToolResultMaintenanceView,
    delegation_admission,
)


def _tool(name: str, arguments: dict | None):
    """Build a minimal object with .name / .arguments (like ToolCallRequest)."""
    return type("TC", (), {"name": name, "arguments": arguments})()


def _batch():
    """Two tool calls with stable results — used for repeated-round tests."""
    return (
        [_tool("read_file", {"path": "x.py"}), _tool("grep", {"q": "foo"})],
        ["content-1", "hits"],
        [{"name": "read_file", "status": "ok"}, {"name": "grep", "status": "ok"}],
    )


# -- EvidenceLedger ------------------------------------------------------------


def test_score_round_four_buckets():
    """New call → new evidence; identical → repeated; same call new result → partial."""
    ledger = EvidenceLedger()
    scores = [
        ledger.score_round([("read_file", {"path": "x.py"}, "content-1")]),  # 全新调用
        ledger.score_round([("read_file", {"path": "x.py"}, "content-1")]),  # 完全相同
        ledger.score_round(
            [("read_file", {"path": "x.py"}, "content-CHANGED")]
        ),  # 新结果
        ledger.score_round([("grep", {"q": "foo"}, "hits")]),  # 全新调用
        ledger.score_round([]),  # 空轮
    ]
    assert scores == [
        SCORE_NEW_EVIDENCE,
        SCORE_REPEATED_CALLS,
        SCORE_PARTIAL_EVIDENCE,
        SCORE_NEW_EVIDENCE,
        SCORE_ZERO_EVIDENCE,
    ]


def test_score_round_compare_before_record():
    """Regression: scoring must not pollute the seen-sets it is judged against."""
    ledger = EvidenceLedger()
    assert (
        ledger.score_round([("read_file", {"path": "x.py"}, "c")]) == SCORE_NEW_EVIDENCE
    )
    assert (
        ledger.score_round([("read_file", {"path": "x.py"}, "c")])
        == SCORE_REPEATED_CALLS
    )


def test_record_tracks_rounds():
    ledger = EvidenceLedger()
    ledger.record([("ls", None, "out")])
    ledger.record([("ls", None, "out")])
    assert ledger.rounds == 2


# -- ProgressGuard -------------------------------------------------------------


def test_progress_guard_escalation_2_4_6():
    """Consecutive zero-evidence rounds escalate: streak 2→nudge, 4→strategy, 6→force."""
    pg = ProgressGuard()
    seq = []
    for _ in range(6):
        iv = pg.observe(SCORE_REPEATED_CALLS)
        seq.append(iv.level if iv else 0)
    assert seq == [0, 1, 0, 2, 0, 3], seq  # 只在 2/4/6 触发，级别 1/2/3
    assert pg.blocked is True and pg.streak == 6


def test_progress_guard_streak_resets_on_new_evidence():
    pg = ProgressGuard()
    pg.observe(SCORE_REPEATED_CALLS)
    pg.observe(SCORE_REPEATED_CALLS)
    iv = pg.observe(SCORE_NEW_EVIDENCE)
    assert iv is None and pg.streak == 0 and pg.blocked is False


# -- StormBreaker --------------------------------------------------------------


def test_storm_breaker_trips_after_three_failures_once():
    sb = StormBreaker()
    seen = []
    for _ in range(5):
        msg = sb.observe_batch(["bash", "grep"], failed=True)
        seen.append(msg is not None)
    assert seen == [False, False, False, True, False], seen  # 只在第 4 次宣布一次
    assert sb.blocked is True


def test_storm_breaker_success_resets_failure_count():
    sb = StormBreaker()
    sb.observe_batch(["bash"], failed=True)
    sb.observe_batch(["bash"], failed=False)  # 成功批次重置
    sb.observe_batch(["bash"], failed=True)
    assert sb.blocked is False


# -- delegation_admission ------------------------------------------------------


def test_delegation_admission_branches():
    assert delegation_admission("请调研一下这个库", fork_turns="none") == (
        "allow",
        "user_requested_research",
    )
    assert delegation_admission(
        "看下 https://example.com 的文档", fork_turns="none"
    ) == (
        "allow",
        "external_source_cited",
    )
    assert delegation_admission("按上面的讨论实现", fork_turns="none") == (
        "deny",
        "task_references_parent_context",
    )
    assert delegation_admission("按上面的讨论实现", fork_turns="all") == (
        "allow",
        "context_inherited",
    )
    assert delegation_admission("实现一个排序算法", fork_turns="none") == (
        "allow",
        "self_contained",
    )
    assert delegation_admission("", fork_turns="none") == ("deny", "empty_task")


# -- LoopGuards.observe_batch 集成 ---------------------------------------------


def test_observe_batch_nudge_fires_on_third_repeated_round():
    """REASONIX 语义：nudge 在 streak==2（连续 2 轮零进展）触发，即第 3 轮。"""
    lg = LoopGuards()
    injs1 = lg.observe_batch(*_batch())  # 新证据
    assert injs1 == [], injs1
    injs2 = lg.observe_batch(*_batch())  # 首次重复 → streak=1，无注入
    assert injs2 == [] and lg.streak == 1, (injs2, lg.streak)
    injs3 = lg.observe_batch(*_batch())  # 二次重复 → streak=2，nudge 注入
    assert len(injs3) == 1 and injs3[0]["role"] == "user" and lg.streak == 2, (
        injs3,
        lg.streak,
    )
    assert "[progress guard]" in injs3[0]["content"]


def test_observe_batch_storm_injection():
    lg = LoopGuards()
    calls, results, _ = _batch()
    events = [{"name": "bash", "status": "error"}] * 4
    messages: list[dict[str, str]] = []
    for _ in range(4):
        messages.extend(lg.observe_batch(calls, results, events))
    storm_msgs = [
        m for m in messages if "storm" in m["content"] or "loop guard" in m["content"]
    ]
    assert len(storm_msgs) == 1  # 熔断只宣布一次


def test_guards_blocked_short_circuits():
    lg = LoopGuards()
    # 第 1 轮是新证据（streak 清零）；从第 2 轮起连续 6 轮重复 → streak=6 → 熔断
    for _ in range(7):
        lg.observe_batch(*_batch())
    assert lg.blocked is True
    assert lg.streak == 6


# -- ContextualToolGate --------------------------------------------------------


def test_contextual_tool_gate_default_allows_everything():
    gate = ContextualToolGate()
    assert gate.check("bash", {"command": "ls"}) is None
    assert gate.blocks == {}


def test_contextual_tool_gate_blocks_on_matcher():
    # 匹配器对 "bash" 返回非 None → 阻断；其他工具放行
    def deny_bash(args: dict) -> str | None:
        command = str(args.get("command") or "")
        if "rm -rf" in command:
            return "destructive command not allowed in this context"
        return None

    gate = ContextualToolGate(matchers={"bash*": deny_bash})
    assert gate.check("bash", {"command": "ls"}) is None
    message = gate.check("bash", {"command": "rm -rf /tmp/x"})
    assert message is not None
    assert "blocked: tool 'bash' is unavailable" in message
    assert "destructive command" in message
    assert gate.blocks == {"bash": 1}


# -- MutationDependencyBarrier -------------------------------------------------


def test_mutation_barrier_soft_reminder_default():
    barrier = MutationDependencyBarrier()  # hard_block=False（默认软提醒）
    barrier.observe_mutation("write_file", {"path": "src/main.py"})
    assert barrier.pending == {"src/main.py": "write_file"}
    message = barrier.check("bash", {"command": "python src/main.py"})
    assert message is not None
    assert "[shell_preflight]" in message
    assert "unverified mutations" in message
    assert "src/main.py" in message


def test_mutation_barrier_hard_block():
    barrier = MutationDependencyBarrier(hard_block=True)
    barrier.observe_mutation("edit_file", {"path": "config.yaml"})
    message = barrier.check("bash", {"command": "cat config.yaml"})
    assert message is not None
    assert (
        "[shell_preflight] blocked tool execution pending dependent mutations"
        in message
    )


def test_mutation_barrier_verify_clears_pending():
    barrier = MutationDependencyBarrier()
    barrier.observe_mutation("write_file", {"path": "src/main.py"})
    barrier.observe_verify("read_file", {"path": "src/main.py"})
    assert barrier.pending == {}
    assert barrier.check("bash", {"command": "python src/main.py"}) is None


def test_mutation_barrier_non_shell_untouched():
    barrier = MutationDependencyBarrier()
    barrier.observe_mutation("write_file", {"path": "a.py"})
    assert barrier.check("read_file", {"path": "a.py"}) is None  # 非 shell 工具不预检


# -- DeliveryPolicyGates -------------------------------------------------------


def test_delivery_policy_last_match_wins():
    gates = DeliveryPolicyGates(
        policies={"*": "allow", "remember": "deny", "web_*": "deny"}
    )
    assert gates.check("read_file", {}) is None
    message = gates.check("remember", {"text": "x"})
    assert message is not None
    assert "[delivery] tool 'remember' rejected" in message
    assert "id=1" in message
    # last-match-wins：web_search 命中 "*"（allow）与 "web_*"（deny），后者生效
    message2 = gates.check("web_search", {"q": "x"})
    assert message2 is not None
    assert gates.rejections == {"remember": 1, "web_search": 1}


def test_delivery_policy_deny_memory_tools():
    gates = DeliveryPolicyGates(deny_memory_tools=True)
    message = gates.check("remember", {"text": "x"})
    assert message is not None
    assert "<memory-tools>" in message
    assert gates.check("read_file", {}) is None


def test_delivery_policy_callable():
    def deny_big(args: dict) -> bool:
        return len(str(args.get("text") or "")) > 10

    gates = DeliveryPolicyGates(policies={"memorize": deny_big})
    assert gates.check("memorize", {"text": "short"}) is None
    assert gates.check("memorize", {"text": "this is a long text"}) is not None


# -- RecoveryGate --------------------------------------------------------------


def test_recovery_gate_noop_without_callback():
    gate = RecoveryGate()
    assert gate.check("bash", {}) is None


def test_recovery_gate_blocks_when_recovery_needed():
    gate = RecoveryGate(recovery_check=lambda: "subagent not finalized")
    message = gate.check("bash", {"command": "ls"})
    assert message == "recovery required before continuing: subagent not finalized"


# -- ToolResultMaintenanceView -------------------------------------------------


def test_result_view_mark_detects_new_results():
    view = ToolResultMaintenanceView()
    assert view.mark("read_file", {"path": "x.py"}, "content-1") is True
    assert view.mark("read_file", {"path": "x.py"}, "content-1") is False  # 完全重复
    assert view.mark("read_file", {"path": "x.py"}, "content-CHANGED") is True  # 新结果
    assert view.has_changed("read_file", {"path": "x.py"}, "content-2") is True


def test_result_view_fingerprint_includes_arguments():
    view = ToolResultMaintenanceView()
    view.mark("grep", {"q": "foo"}, "hits-a")
    # 相同结果但不同参数 → 视为新调用
    assert view.mark("grep", {"q": "bar"}, "hits-a") is True


# -- LoopGuards.check_tool 链 --------------------------------------------------


def test_check_tool_chains_all_gates():
    lg = LoopGuards(
        recovery=RecoveryGate(recovery_check=lambda: "pending approval"),
        contextual=ContextualToolGate(),
        mutation=MutationDependencyBarrier(),
        delivery=DeliveryPolicyGates(policies={"remember": "deny"}),
    )
    # RecoveryGate 最先触发
    assert (
        lg.check_tool("bash", {})
        == "recovery required before continuing: pending approval"
    )


def test_check_tool_delivery_gate_after_recovery_ok():
    lg = LoopGuards(delivery=DeliveryPolicyGates(policies={"remember": "deny"}))
    assert lg.check_tool("read_file", {}) is None
    message = lg.check_tool("remember", {"text": "x"})
    assert message is not None
    assert "[delivery]" in message


def test_observe_tool_mutation_and_result_through_loop_guards():
    lg = LoopGuards()
    lg.observe_tool_result("read_file", {"path": "x.py"}, "content-1")
    assert lg.observe_tool_result("read_file", {"path": "x.py"}, "content-1") is False
    lg.observe_tool_mutation("write_file", {"path": "y.py"})
    assert lg._mutation.pending == {"y.py": "write_file"}  # 内部状态可达（测试专用）
    message = lg.check_tool("bash", {"command": "run y.py"})
    assert message is not None
    assert "[shell_preflight]" in message
