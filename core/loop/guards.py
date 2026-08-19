"""Loop guards (P3.5) — REASONIX 反漫游守卫的 DEEPCODE 落地。

来源：``scripts/REASONIX_第五阶段深度分析报告.md`` / ``REASONIX_第六阶段深度分析报告.md``
（逆向 reasonix.exe v1.21.5 的 applyBatchGuards 组件族）。

落地的四个机制：

1. :class:`EvidenceLedger` + :func:`EvidenceLedger.score_round`
   — 证据账本（``evidence.(*Ledger).Record`` port，纯内存追加）+ 整轮 0-3 评分。
2. :class:`ProgressGuard` — 连续零进展轮次 N，2/4/6 升级干预
   （``progressGuard.observe`` port：N==2 温和提醒 / N==4 强制换策略 / N==6 强制收尾+阻塞）。
3. :class:`StormBreaker` — 工具序列指纹（``batchStormSignature`` port：
   concat 工具名），相同失败签名超过上限熔断。
4. :func:`delegation_admission` — 委派准入（``agent.delegationAdmission`` port，
   已按 DEEPCODE 本地并发委派场景适配）。

评分方向裁决
------------

第五阶段 ``progressGuard.observe`` 反编译伪代码：

    iVar4 = evidence.(*ProgressTracker).ScoreRound(guard[0], toolCalls, count, ctx);
    if (iVar4 < 1) guard[1] += 1;   // 零新证据 → 连续计数++
    else           guard[1] = 0;    // 有新证据 → 计数清零

证明 ScoreRound 的**聚合返回值**语义为：``0`` = 本轮零证据（最差，streak++），
``>= 1`` = 本轮有进展（streak 清零）。第六阶段 ``scoreReceipt`` 的单条打分标签
（0=新证据 / 3=零证据）是 receipt 层的惩罚分，与聚合层方向相反（聚合时被反转）。

落地以第五阶段伪代码为准：``SCORE_ZERO_EVIDENCE == 0``（最差），
``SCORE_NEW_EVIDENCE == 3``（最好）。同时把 ``SCORE_REPEATED_CALLS``（完全重复）
也视为零进展——REASONIX 干预消息原文明确把"repeated earlier reads or commands
without new results"归入零进展，故 ``score < SCORE_PARTIAL_EVIDENCE`` 即触发 streak++。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# 评分常量（0 = 最差 / 零证据，3 = 最好 / 新证据）——方向裁决见模块 docstring
# ---------------------------------------------------------------------------

SCORE_ZERO_EVIDENCE = 0  # 完全零证据（双零：无新文件无新结果）
SCORE_REPEATED_CALLS = 1  # 重复工具调用（无新产出）
SCORE_PARTIAL_EVIDENCE = 2  # 部分证据（相同调用但结果变化）
SCORE_NEW_EVIDENCE = 3  # 本轮有新证据（正常）

# ProgressGuard 升级阈值（REASONIX 第五阶段 §2.2）
GUARD_NUDGE = 2  # 连续 N 轮零进展 → 温和提醒
GUARD_STRATEGY = 4  # → 强制换策略
GUARD_FORCE_ANSWER = 6  # → 强制出最终答案 + 阻塞

# 委派准入白名单（REASONIX 第六阶段 §2.2，6 条全部保留）
DELEGATION_RESEARCH_HINTS = (
    "research",
    "调研",
    "查资料",
    "查文档",
    "search the web",
    "look up online",
)
# DEEPCODE 适配：引用父上下文但不继承上下文 → 子 agent 缺上下文无法完成
DELEGATION_CONTEXT_REFERENCE_HINTS = (
    "上述",
    "上面",
    "之前说的",
    "前面提到",
    "我们刚才",
    "我们之前",
    "之前的讨论",
    "前面",
    "above",
    "mentioned",
    "earlier",
)

# 工具结果哈希截断长度（对齐 backpressure._TAIL_CHARS 的务实上限）
_RESULT_HASH_CHARS = 2000


def tool_call_signature(name: str, arguments: dict[str, Any] | None) -> str:
    """规范化工具调用指纹：工具名 + 排序键的参数 JSON。"""
    args = arguments or {}
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return f"{name}:{canon}"


def result_hash(result: Any) -> str | None:
    """工具结果摘要哈希；空结果返回 None（无法作为证据）。"""
    text = str(result)
    if not text.strip():
        return None
    return hashlib.sha256(
        text[:_RESULT_HASH_CHARS].encode("utf-8", "ignore")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """单条工具调用证据（对齐 REASONIX Ledger 条目语义）。"""

    tool: str
    arg_signature: str
    result_hash: str | None


class EvidenceLedger:
    """内存追加证据账本（纯内存，不落盘——同 REASONIX Ledger）。"""

    def __init__(self) -> None:
        self._seen_calls: set[tuple[str, str]] = set()
        self._seen_results: set[str] = set()
        self._rounds: list[list[ToolEvidence]] = []

    @staticmethod
    def _build(
        calls: Iterable[tuple[str, dict[str, Any] | None, Any]]
    ) -> list[ToolEvidence]:
        """把 (tool_name, arguments, result) 三元组构建为证据列表（不落账）。"""
        evidence: list[ToolEvidence] = []
        for name, arguments, result in calls:
            evidence.append(
                ToolEvidence(
                    tool=name,
                    arg_signature=tool_call_signature(name, arguments),
                    result_hash=result_hash(result),
                )
            )
        return evidence

    def _append(self, evidence: list[ToolEvidence]) -> None:
        """把一轮证据写入账本（seen 集合 + 轮次历史）。"""
        for item in evidence:
            self._seen_calls.add((item.tool, item.arg_signature))
            if item.result_hash is not None:
                self._seen_results.add(item.result_hash)
        if evidence:
            self._rounds.append(evidence)

    def record(self, calls: Iterable[tuple[str, dict[str, Any] | None, Any]]) -> list[ToolEvidence]:
        """记录一轮 (tool_name, arguments, result) 三元组，返回本轮证据列表。"""
        evidence = self._build(calls)
        self._append(evidence)
        return evidence

    def score_round(
        self, calls: Iterable[tuple[str, dict[str, Any] | None, Any]]
    ) -> int:
        """整轮评分 0-3（0=零证据最差，3=新证据最好）。

        先与历史证据比对、后落账（compare-then-record）：若先写入
        ``_seen_calls``/``_seen_results`` 再查重，本轮证据必然命中自身，
        导致永远返回 ``SCORE_REPEATED_CALLS``——进度守卫会把每一轮都
        当作零进展轮。
        """
        evidence = self._build(calls)
        if not evidence:
            return SCORE_ZERO_EVIDENCE
        for item in evidence:
            if (item.tool, item.arg_signature) not in self._seen_calls:
                # 存在全新调用（工具+参数都未出现过）→ 本轮有新证据
                self._append(evidence)
                return SCORE_NEW_EVIDENCE
        for item in evidence:
            if item.result_hash is not None and item.result_hash not in self._seen_results:
                # 相同调用但产生了新的结果内容 → 部分证据
                self._append(evidence)
                return SCORE_PARTIAL_EVIDENCE
        self._append(evidence)
        return SCORE_REPEATED_CALLS

    @property
    def rounds(self) -> int:
        return len(self._rounds)


@dataclass(frozen=True, slots=True)
class GuardIntervention:
    """一次进度守卫干预。"""

    level: int  # 1=温和提醒 2=强制换策略 3=强制收尾+阻塞
    streak: int  # 触发时的连续零进展轮数
    message: str


class ProgressGuard:
    """连续零进展轮次守卫（``progressGuard.observe`` port）。

    每轮评分后调用 :meth:`observe`：``score < SCORE_PARTIAL_EVIDENCE``
    （零证据或完全重复）→ streak++；否则 streak 清零。
    阈值 2/4/6 各触发一次（``>=`` 判定 + 级别递进，避免跳级漏触发/重复触发）。
    """

    def __init__(
        self,
        nudge: int = GUARD_NUDGE,
        strategy: int = GUARD_STRATEGY,
        force_answer: int = GUARD_FORCE_ANSWER,
    ) -> None:
        self._nudge = nudge
        self._strategy = strategy
        self._force = force_answer
        self._streak = 0
        self._delivered = 0
        self._blocked = False

    @property
    def streak(self) -> int:
        return self._streak

    @property
    def blocked(self) -> bool:
        """N==6 触发后为 True（强制收尾熔断）。"""
        return self._blocked

    def observe(self, score: int) -> GuardIntervention | None:
        if score < SCORE_PARTIAL_EVIDENCE:
            self._streak += 1
        else:
            self._streak = 0
            return None

        if self._streak >= self._force and self._delivered < 3:
            self._delivered = 3
            self._blocked = True
            return GuardIntervention(
                3,
                self._streak,
                f"[progress guard] 连续 {self._streak} 轮工具调用未产生任何新证据"
                "（没有新文件、新结果或变更）。请停止探索，现在直接给出最终答案，"
                "说明已确认的内容与仍然未知的内容。",
            )
        if self._streak >= self._strategy and self._delivered < 2:
            self._delivered = 2
            return GuardIntervention(
                2,
                self._streak,
                f"[progress guard] 连续 {self._streak} 轮仍无新证据。请立即更换策略："
                "换一个角度或工具、委派一个聚焦的子任务、或缩小验证范围后再继续。",
            )
        if self._streak >= self._nudge and self._delivered < 1:
            self._delivered = 1
            return GuardIntervention(
                1,
                self._streak,
                f"[progress guard] 最近 {self._streak} 轮工具调用反复执行了相同的"
                "读取/命令，但没有产生新结果。请收窄调查范围或调整计划后再继续。",
            )
        return None


class StormBreaker:
    """工具序列风暴熔断（``batchStormSignature`` port）。

    同批工具名 concat 成签名；同一失败签名超过 ``max_failures`` 次后熔断
    （首次熔断注入一次提醒，之后保持 blocked 不再重复注入）。
    """

    def __init__(self, max_failures: int = 3) -> None:
        self._max_failures = max_failures
        self._fail_counts: dict[str, int] = {}
        self._blocked = False
        self._announced = False

    @property
    def blocked(self) -> bool:
        return self._blocked

    def observe_batch(self, tool_names: list[str], *, failed: bool) -> str | None:
        """返回熔断提醒消息（仅首次熔断时）；否则 None。"""
        if not failed or not tool_names:
            return None
        signature = "|".join(tool_names)
        count = self._fail_counts.get(signature, 0) + 1
        self._fail_counts[signature] = count
        if count > self._max_failures:
            self._blocked = True
            if not self._announced:
                self._announced = True
                return (
                    f"[loop guard] 工具序列 {signature} 已连续失败 {count} 次"
                    f"（超过 {self._max_failures} 次上限），已熔断。请停止重复该序列，"
                    "改用不同的方法。"
                )
        return None


def delegation_admission(
    task: str, *, fork_turns: str = "none"
) -> tuple[str, str]:
    """委派准入（``agent.delegationAdmission`` 的 DEEPCODE 适配版）。

    REASONIX 原版：委派场景下仅研究意图 / 外部 URL 引用放行，否则
    ``deny(local_fix_no_external_need)``。DEEPCODE 的 spawn_agent 是本地并发委派
    （C2，非外部研究委派），照搬原版会把普通编程子任务全部拒绝，故适配为：

    - 研究意图 / 外部 URL / 继承父上下文 → ``allow``
    - 引用父上下文但不继承（fork_turns=none）→ ``deny``（子 agent 缺上下文无法完成）
    - 其余自包含任务 → ``allow``

    返回 ``(decision, reason)``，decision ∈ {"allow", "deny"}。
    """
    text = (task or "").strip()
    if not text:
        return "deny", "empty_task"
    low = text.lower()
    if any(hint in low for hint in DELEGATION_RESEARCH_HINTS):
        return "allow", "user_requested_research"
    if "http://" in low or "https://" in low:
        return "allow", "external_source_cited"
    if fork_turns != "none":
        return "allow", "context_inherited"
    if any(hint in text for hint in DELEGATION_CONTEXT_REFERENCE_HINTS):
        return "deny", "task_references_parent_context"
    return "allow", "self_contained"


# ---------------------------------------------------------------------------
# P3.6 单工具级守卫（REASONIX 第五阶段 §1.2–1.8 port）——runner 在工具执行前
# 调用 ``LoopGuards.check_tool``，执行后调用 ``observe_tool_*`` 观察。
# ---------------------------------------------------------------------------


def _extract_paths(arguments: dict[str, Any] | None) -> list[str]:
    """从工具参数中提取显式路径（path / file_path / filepath / file）。"""
    args = arguments or {}
    paths: list[str] = []
    for key in ("path", "file_path", "filepath", "file"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    return paths


class ContextualToolGate:
    """上下文工具门（``applyContextualToolGate`` / ``contextualToolGateOutcome`` port）。

    REASONIX 侧按工具类型 hash 分派到专用匹配器，匹配器判定当前工作流上下文
    是否允许该工具执行，不匹配 → ``blocked: tool %q is unavailable in the current
    workflow context``（第六阶段 §3）。DEEPCODE 侧以 ``{工具 glob: 匹配器回调}``
    注册表等价实现，回调返回非 None 即阻断（str 为原因）。默认空注册表 → 零成本。
    """

    def __init__(
        self,
        matchers: dict[str, Callable[[dict[str, Any]], str | None]] | None = None,
    ) -> None:
        self._matchers = dict(matchers or {})
        self._lock = threading.Lock()
        self._blocks: dict[str, int] = {}

    def check(self, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
        """工具执行前调用；返回 None=放行，str=阻断消息（errors-as-data）。"""
        args = arguments or {}
        for pattern, matcher in self._matchers.items():
            if fnmatch.fnmatch(tool_name, pattern):
                reason = matcher(args)
                if reason is not None:
                    with self._lock:
                        self._blocks[tool_name] = self._blocks.get(tool_name, 0) + 1
                    return (
                        f"blocked: tool {tool_name!r} is unavailable in the current "
                        f"workflow context ({reason})"
                    )
        return None

    @property
    def blocks(self) -> dict[str, int]:
        """各工具被阻断次数（只读快照）。"""
        with self._lock:
            return dict(self._blocks)


class MutationDependencyBarrier:
    """bash 变更依赖屏障（``applyMutationDependencyBarrier`` / ``shellPreflightExecution`` port）。

    REASONIX 语义：bash 工具执行前预检"挂起的变更依赖"，存在依赖则阻断并记日志
    ``[shell_preflight] blocked tool execution pending dependent mutations (bash %s)``。
    DEEPCODE 适配：写/编辑类工具先 ``observe_mutation`` 登记待验证路径，read/grep/
    test 等验证工具 ``observe_verify`` 清除；bash/exec 引用未验证路径时——默认**软提醒**
    （开发循环 write→test 是合法路径，硬阻断会误伤），``hard_block=True`` 才做 REASONIX
    等价硬阻断。
    """

    # 会登记 pending 路径的变更工具
    _MUTATION_TOOLS = (
        "write_file", "write", "edit_file", "edit", "apply_patch", "patch", "replace",
    )
    # 会清除 pending 的验证工具
    _VERIFY_TOOLS = ("read_file", "read", "grep", "test", "pytest", "run_tests")
    # 触发依赖预检的 shell 工具
    _SHELL_TOOLS = ("bash", "exec", "execute_bash", "execute_commands")

    def __init__(self, hard_block: bool = False) -> None:
        self._hard_block = hard_block
        self._pending: dict[str, str] = {}  # path -> 登记它的变更工具名
        self._lock = threading.Lock()

    def observe_mutation(self, tool_name: str, arguments: dict[str, Any] | None) -> None:
        """变更工具执行成功后登记待验证路径。"""
        if tool_name not in self._MUTATION_TOOLS:
            return
        with self._lock:
            for path in _extract_paths(arguments):
                self._pending[path] = tool_name

    def observe_verify(self, tool_name: str, arguments: dict[str, Any] | None) -> None:
        """验证工具（read/grep/test）命中 pending 路径时清除依赖。"""
        if tool_name not in self._VERIFY_TOOLS:
            return
        with self._lock:
            for path in _extract_paths(arguments):
                self._pending.pop(path, None)

    def check(self, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
        """bash/exec 执行前预检；返回 None=放行，str=提醒/阻断消息。"""
        if tool_name not in self._SHELL_TOOLS:
            return None
        args = arguments or {}
        command = str(args.get("command") or args.get("cmd") or "")
        with self._lock:
            hits = [path for path in self._pending if path and path in command]
        if not hits:
            return None
        pending = ", ".join(hits[:3])
        registrar = self._pending.get(hits[0], "?")
        if self._hard_block:
            return (
                f"[shell_preflight] blocked tool execution pending dependent "
                f"mutations (bash {tool_name}): {pending}"
            )
        return (
            f"[shell_preflight] {tool_name} references unverified mutations "
            f"({pending}) written by {registrar}. "
            "Verify them first (read_file/grep/test) or proceed if intentional."
        )

    @property
    def pending(self) -> dict[str, str]:
        """挂起路径快照（只读）。"""
        with self._lock:
            return dict(self._pending)


class DeliveryPolicyGates:
    """配送策略门（``applyDeliveryPolicyGates`` port）。

    REASONIX 语义：按配送策略检查工具调用，``remember`` 等记忆工具特判，消息
    ``[delivery] tool %q rejected by delivery policy %q: %s (id=%d)``（第五阶段 §1.7）。
    DEEPCODE 侧以 ``{工具 glob: 策略}`` 实现：策略为 "allow"/"deny" 或回调
    ``Callable[[dict], bool]``（True=deny）。last-match-wins（与 PermissionEngine 一致）。
    ``deny_memory_tools=True`` 时对记忆类工具特判。
    """

    _MEMORY_TOOLS = ("remember", "memorize", "memory_save", "memory_store")

    def __init__(
        self,
        policies: dict[str, str | Callable[[dict[str, Any]], bool]] | None = None,
        *,
        deny_memory_tools: bool = False,
    ) -> None:
        self._policies = dict(policies or {})
        self._deny_memory_tools = deny_memory_tools
        self._lock = threading.Lock()
        self._rejections: dict[str, int] = {}

    def check(self, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
        args = arguments or {}
        decision: str | None = None
        matched: str | None = None
        for pattern, policy in self._policies.items():
            if fnmatch.fnmatch(tool_name, pattern):
                matched = pattern
                if callable(policy):
                    decision = "deny" if policy(args) else "allow"
                else:
                    decision = policy
        if self._deny_memory_tools and tool_name in self._MEMORY_TOOLS:
            matched = "<memory-tools>"
            decision = "deny"
        if decision != "deny":
            return None
        with self._lock:
            count = self._rejections.get(tool_name, 0) + 1
            self._rejections[tool_name] = count
        return (
            f"[delivery] tool {tool_name!r} rejected by delivery policy "
            f"{matched!r}: not allowed in the current delivery policy (id={count})"
        )

    @property
    def rejections(self) -> dict[str, int]:
        """各工具被拒次数（只读快照）。"""
        with self._lock:
            return dict(self._rejections)


class RecoveryGate:
    """恢复门（``applyRecoveryAndPermission`` 的 RecoveryGate 部分 port）。

    REASONIX 语义：``recovery required before continuing: %s``（第五阶段 §1.7），
    恢复未完成时阻断工具执行。DEEPCODE 侧通过 ``recovery_check`` 回调注入
    "是否需要恢复"的判定（如子任务未收尾/审批未决），无回调 → 零成本。
    """

    def __init__(self, recovery_check: Callable[[], str | None] | None = None) -> None:
        self._recovery_check = recovery_check

    def check(self, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
        if self._recovery_check is None:
            return None
        need = self._recovery_check()
        if need:
            return f"recovery required before continuing: {need}"
        return None


class ToolResultMaintenanceView:
    """工具结果维护视图（``applyToolResultMaintenanceView`` port）。

    REASONIX 语义：基于 ``<evidence fingerprint>``（第五阶段 §1.7）对工具结果做
    缓存指纹比对，维护"同一调用是否产生新结果"的视图。DEEPCODE 侧以
    调用签名 → 结果指纹 的简单 dict 缓存实现（锁保护），``mark`` 返回是否为新结果。
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def fingerprint(self, tool_name: str, arguments: dict[str, Any] | None, result: Any) -> str:
        """结果指纹 = 调用签名 + 结果哈希。"""
        return (
            f"{tool_call_signature(tool_name, arguments)}|"
            f"{result_hash(result) or '<empty>'}"
        )

    def has_changed(self, tool_name: str, arguments: dict[str, Any] | None, result: Any) -> bool:
        """是否与最近一次标记的结果不同。"""
        key = tool_call_signature(tool_name, arguments)
        fp = self.fingerprint(tool_name, arguments, result)
        with self._lock:
            return self._cache.get(key) != fp

    def mark(self, tool_name: str, arguments: dict[str, Any] | None, result: Any) -> bool:
        """标记结果指纹；返回 True=相对上次有新结果，False=完全重复。"""
        key = tool_call_signature(tool_name, arguments)
        fp = self.fingerprint(tool_name, arguments, result)
        with self._lock:
            changed = self._cache.get(key) != fp
            self._cache[key] = fp
        return changed


class LoopGuards:
    """REASONIX ``applyBatchGuards`` 的 Python 化组合：进度守卫 + 风暴熔断。

    runner 主循环工具执行后调用 :meth:`observe_batch`，返回注入消息列表
    （``{"role": "user", "content": ...}``），由调用方 append 进 messages。
    """

    def __init__(
        self,
        progress: ProgressGuard | None = None,
        storm: StormBreaker | None = None,
        contextual: ContextualToolGate | None = None,
        mutation: MutationDependencyBarrier | None = None,
        delivery: DeliveryPolicyGates | None = None,
        recovery: RecoveryGate | None = None,
        result_view: ToolResultMaintenanceView | None = None,
    ) -> None:
        self._progress = progress or ProgressGuard()
        self._storm = storm or StormBreaker()
        self._ledger = EvidenceLedger()
        self._contextual = contextual or ContextualToolGate()
        self._mutation = mutation or MutationDependencyBarrier()
        self._delivery = delivery or DeliveryPolicyGates()
        self._recovery = recovery or RecoveryGate()
        self._result_view = result_view or ToolResultMaintenanceView()

    @property
    def progress(self) -> ProgressGuard:
        return self._progress

    @property
    def storm(self) -> StormBreaker:
        return self._storm

    @property
    def ledger(self) -> EvidenceLedger:
        return self._ledger

    @property
    def blocked(self) -> bool:
        """进度守卫熔断（N==6 强制收尾）——runner 以此短路后续工具执行。"""
        return self._progress.blocked

    @property
    def streak(self) -> int:
        return self._progress.streak

    def observe_batch(
        self,
        tool_calls: Iterable[Any],
        results: Iterable[Any],
        events: Iterable[dict[str, str]],
    ) -> list[dict[str, str]]:
        """工具执行完成后调用，返回注入消息列表（可为空）。

        ``tool_calls`` 元素需带 ``.name`` 与 ``.arguments``（或 dict 的 "name"/"arguments"）。
        """
        calls = [
            (
                getattr(tc, "name", None) or (tc.get("name") if isinstance(tc, dict) else None),
                getattr(tc, "arguments", None) or (tc.get("arguments") if isinstance(tc, dict) else None),
                result,
            )
            for tc, result in zip(tool_calls, results)
        ]
        injections: list[dict[str, str]] = []

        score = self._ledger.score_round(calls)
        intervention = self._progress.observe(score)
        if intervention is not None:
            injections.append({"role": "user", "content": intervention.message})

        names = [c[0] for c in calls if c[0]]
        failed = any((e.get("status") == "error") for e in events)
        storm_message = self._storm.observe_batch(names, failed=failed)
        if storm_message is not None:
            injections.append({"role": "user", "content": storm_message})

        return injections

    def check_tool(self, tool_name: str, arguments: dict[str, Any] | None) -> str | None:
        """工具执行前的单工具守卫链（runner ``_run_tool`` 调用）。

        链序：RecoveryGate（恢复未完成）→ ContextualToolGate（上下文可用性）→
        MutationDependencyBarrier（bash 变更依赖预检）→ DeliveryPolicyGates
        （配送策略）。返回 None=放行，str=阻断消息（errors-as-data）。
        """
        for gate in (self._recovery, self._contextual, self._mutation, self._delivery):
            message = gate.check(tool_name, arguments)
            if message is not None:
                return message
        return None

    def observe_tool_mutation(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> None:
        """变更工具执行成功后登记待验证路径（MutationDependencyBarrier）。"""
        self._mutation.observe_mutation(tool_name, arguments)

    def observe_tool_verify(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> None:
        """验证工具执行成功后清除已验证路径（MutationDependencyBarrier）。"""
        self._mutation.observe_verify(tool_name, arguments)

    def observe_tool_result(
        self, tool_name: str, arguments: dict[str, Any] | None, result: Any
    ) -> bool:
        """标记工具结果指纹（ToolResultMaintenanceView）；返回是否为新结果。"""
        return self._result_view.mark(tool_name, arguments, result)


__all__ = [
    "SCORE_ZERO_EVIDENCE",
    "SCORE_REPEATED_CALLS",
    "SCORE_PARTIAL_EVIDENCE",
    "SCORE_NEW_EVIDENCE",
    "GUARD_NUDGE",
    "GUARD_STRATEGY",
    "GUARD_FORCE_ANSWER",
    "DELEGATION_RESEARCH_HINTS",
    "DELEGATION_CONTEXT_REFERENCE_HINTS",
    "ToolEvidence",
    "EvidenceLedger",
    "GuardIntervention",
    "ProgressGuard",
    "StormBreaker",
    "delegation_admission",
    "LoopGuards",
    "tool_call_signature",
    "result_hash",
    "ContextualToolGate",
    "MutationDependencyBarrier",
    "DeliveryPolicyGates",
    "RecoveryGate",
    "ToolResultMaintenanceView",
]
