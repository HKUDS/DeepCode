# DeepCode Loop Engineering 与 Graph Engineering 调研报告

> 调研日期：2026-07-26
> 资料时间窗口：2026-05-26 至 2026-07-26
> 代码审计对象：`deepcode-desktop` 当前工作树
> 说明：公开研究资料严格限定在调研日前两个月内；近期 arXiv 论文均属于预印本，其实验结果应视为作者报告，而不是已经形成的行业定论。

## 摘要

DeepCode 已经具备真实的 Loop Engineering 骨架，不是普通的“LLM 加几个工具”的聊天壳：

- Agent 内部工具循环已经成熟。
- Goal 支持跨 Turn 自动继续、预算、终止状态和完成评估。
- CLI 与 Desktop 使用同一套 AgentSession 与后端逻辑。
- 权限、审批、Hooks、Skills、持久 Session、安全恢复和 Worktree 隔离均已存在。

但是，DeepCode 目前还不能称为完整、可靠的 Loop Engineering 产品，主要缺口是：

- 完成证据没有和当前源码版本强绑定。
- 验收标准没有逐项绑定验证证据。
- 长任务进度仍然偏文本化，而不是结构化状态。
- Sub-Agent、Goal、Workflow 和 Automation 尚未统一成一张持久化工作图。

DeepCode 目前也尚未达到完整的 Graph Engineering。它已经具有 Goal、Turn、Plan、Sub-Agent、Workflow、Artifact 和 Automation 等图节点雏形，但这些对象还不是一张统一、持久化、可调度、可恢复的权威工作图。

推荐的产品方向是：

> **DeepCode Work Graph：从目标、任务、代码变更到验证证据的深层可追溯工程图。**

这会比单纯增加更多 Agent 或模仿其他 Coding Agent 的界面更有辨识度。

---

## 1. Loop Engineering 的近期定义

### 1.1 Loop Engineering 优化的不是一次模型调用

2026 年 6 月 28 日的研究将 Loop Engineering 的核心对象定义为外部的 `Loop Specification`，至少包含：

- Trigger：什么时候启动。
- Goal：要达成什么结果。
- Verification：怎样证明结果成立。
- Stopping rule：何时停止。
- Memory：下一轮需要保留什么状态。

它特别强调：Loop Engineering 不是普通编程语言里的循环，也不是 Agent Harness 内部已经存在的 perceive-act-observe 工具循环，而是包裹在 Agent Harness 外面的可复用运行协议。

来源：

- [Stop Hand-Holding Your Coding Agent: Engineering the Loops that Replace Step-by-Step Prompting](https://arxiv.org/abs/2607.00038)，提交于 2026-06-28。

IBM 在 2026 年 7 月 17 日发布的定义基本一致：工程师不再逐步提示 Agent，而是设计一个能够自动行动、观察、校验、调整，并在满足目标时终止的系统。其组成包括 Automation、Hooks、Context、Tools、Worktrees、Skills、Subagents 和持久状态 Spine。

来源：

- [IBM：What Is Loop Engineering?](https://www.ibm.com/think/topics/loop-engineering)，发布于 2026-07-17。

### 1.2 Prompt、Context、Harness、Loop 与 Graph 的关系

| 层次 | 优化对象 | 生命周期 |
|---|---|---|
| Prompt Engineering | 一次模型回答 | 单次模型调用 |
| Context Engineering | 一次模型窗口 | 单次调用或单个 Turn |
| Harness Engineering | 一次 Agent 执行 | Agent 工具循环 |
| Loop Engineering | 多轮、多 Turn、可持续任务 | 直到证明完成、阻塞或预算耗尽 |
| Graph Engineering | 多个任务、Agent、证据与治理循环的关系 | 整个系统 |

因此，正常 DeepCode CLI 本来就应该能够处理长任务。`/goal` 或独立的 Loop 命令不应成为“获得 Agent 能力”的开关。

普通 CLI 使用的是 Agent Harness 内循环；Goal 模式增加的是：

- 持久目标。
- 跨 Turn 自动继续。
- Attempt 管理。
- Token、时间和次数预算。
- 完成评估。
- 安全暂停与恢复。

Goal 模式是在普通 Agent Loop 上增加外部生命周期控制，而不是替代普通 CLI。

---

## 2. 最近两个月研究所强调的新方向

### 2.1 从 Agent 声明完成，转向证据驱动完成

2026 年 7 月 16 日的 Proof-or-Stop 提出：

> Agent 所说的“测试通过”“已经完成”“可以合并”都只是 claim，不是系统状态。

系统只有拿到与当前源码状态绑定、足够新鲜、可机械验证的证据，才能允许生命周期进入 `DONE` 或 `READY_TO_MERGE`。

来源：

- [Proof-or-Stop: Don't Trust the Agent, Trust the Evidence](https://arxiv.org/abs/2607.14890)，提交于 2026-07-16。

成熟的 Coding Agent Loop 应当接近：

```text
Goal
  → Work
  → Source revision
  → Verification
  → Evidence receipt
  → Gate decision
  → Complete / Repair / Blocked
```

而不能是：

```text
Agent：“我觉得完成了”
  → Complete
```

### 2.2 Verification 正在成为比代码生成更难的问题

2026 年 6 月 24 日的 Verification Horizon 指出：

- 候选代码生成正在变得容易。
- 验证候选结果是否真正符合用户意图正在变得更难。
- 任何固定测试或 LLM Judge 都只是用户意图的代理。
- 固定 verifier 会出现饱和、奖励欺骗和隐藏失败。
- verifier 必须随着 Agent 能力共同演进。

来源：

- [The Verification Horizon: No Silver Bullet for Coding Agent Rewards](https://arxiv.org/abs/2606.26300)，提交于 2026-06-24。

这意味着仅仅“多跑几次测试”不足以构成产品级可靠性，还需要：

- 多种验证器。
- 独立 Reviewer。
- 隐藏或冻结的验证 Anchor。
- 正向指标对应的 Counter-metric。
- 验证器版本与来源追踪。

### 2.3 确定性控制流应当由 Harness 持有

2026 年 6 月 14 日的 LLM-as-Code 认为，确定性的分支、循环和顺序控制不应完全交给 LLM。程序负责控制流，模型只在需要推理和判断的节点发挥作用。

来源：

- [LLM-as-Code: Agentic Programming for Agent Harness](https://arxiv.org/abs/2606.15874)，提交于 2026-06-14。

2026 年 6 月 25 日的 Deterministic Control Plane 也强调：

- 权限。
- 配置供应链。
- 审计记录。
- Requirement-to-file-to-test traceability。
- 阶段状态机。

这些治理规则应当是确定性的，不能再委托给另一个 LLM 自由判断。

来源：

- [A Deterministic Control Plane for LLM Coding Agents](https://arxiv.org/abs/2606.26924)，提交于 2026-06-25。

### 2.4 长任务需要结构化因果状态

2026 年 7 月 13 日的 StructAgent 将长期任务状态表示为结构化的因果进度状态，并用验证结果控制状态转移，从而支持：

- 明确的任务进度。
- Checkpoint。
- 证据驱动完成。
- 定向失败恢复。
- 可验证的状态变化。

来源：

- [StructAgent: Harness Long-horizon Digital Agents with Unified Causal Structure](https://arxiv.org/abs/2607.11388)，提交于 2026-07-13。

### 2.5 Context 不应只是一段越来越长的聊天记录

2026 年 7 月 1 日的 Self-GC 将用户消息、工具调用、Skill 状态等变成可索引、可折叠、可裁剪、可恢复的对象，而不是把全部历史压缩成一段不可逆摘要。

来源：

- [Self-GC: Self-Governing Context for Long-Horizon LLM Agents](https://arxiv.org/abs/2607.00692)，提交于 2026-07-01。

近期方向正在从：

> 如何把更多文字塞进上下文。

转向：

> 如何让 Agent 只看到当前任务节点需要的结构化状态，同时随时能够找回原始证据。

### 2.6 多 Agent Harness 的价值来自执行拓扑，而不只是更多模型

2026 年 6 月 11 日的 Recursive Agent Harnesses 展示了父 Agent 通过脚本化 Harness 并行派生子 Agent，并将性能提升归因于 Harness 结构，而不是更换模型。

来源：

- [Recursive Agent Harnesses](https://arxiv.org/abs/2606.13643)，提交于 2026-06-11。

这说明多 Agent 的价值不在于“Agent 数量”，而在于：

- 任务是否真正可以独立。
- 上下文是否隔离。
- 依赖是否明确。
- 聚合是否有契约。
- 失败是否能够局部恢复。
- 验证是否独立。

---

## 3. Graph Engineering 的近期含义

### 3.1 当前仍然是非标准术语

Graph Engineering 是 2026 年 7 月才快速出现的行业术语，目前没有形成统一的学术定义。在本调研时间窗口内，一种具有代表性的解释将其分成两层：

- Work Graph。
- Improvement/Governance Graph。

来源：

- [Graph Engineering for AI Agents](https://www.eigent.ai/blog/graph-engineering-ai-agents)，发布于 2026-07-21。

该来源属于行业提出者的解释，不应被误认为已经形成学界共识。

### 3.2 Work Graph

Work Graph 的节点可以包括：

- Goal。
- Task。
- Agent。
- Tool。
- File/Artifact。
- Verification。
- Evidence。
- Human approval。

边可以包括：

- `depends_on`。
- `produced`。
- `verified_by`。
- `blocked_by`。
- `delegated_to`。
- `derived_from`。
- `supersedes`。

一张真正的 Work Graph 应当明确：

- 哪些任务可以并行。
- 哪些任务必须等待依赖完成。
- 哪个 Agent 拥有哪个任务。
- 某个 Artifact 是由哪个任务产生的。
- 某条结论由什么 Evidence 支持。
- 某个节点失败后应当重试、回退还是阻塞。

### 3.3 Improvement/Governance Graph

更高一层是把多个循环连接起来：

- 实现循环。
- 测试循环。
- Review 循环。
- 安全循环。
- 成本控制循环。
- 审计循环。
- 人工决策循环。

同时定义不可由 Agent 自己修改的 Anchor，例如：

- 冻结需求。
- 隐藏测试。
- CI 策略。
- 权限规则。
- 人工判断。
- 真实业务结果。

治理图还需要处理：

- 哪个 Loop 有权修改哪个 Loop。
- 每个 Loop 的运行频率。
- 指标对应的 Counter-metric。
- Verifier 漂移。
- Goodhart 行为。
- 版本、灰度发布与回滚。

### 3.4 Graph Engineering 不等于什么

以下能力本身不等于 Graph Engineering：

- 使用知识图谱。
- 使用 GraphRAG。
- 画一个普通 Task DAG。
- 同时启动多个 Agent。
- 引入 LangGraph。
- 在 Desktop 中显示一个漂亮的 Graph UI。

只有任务、状态、依赖、证据、权限和恢复真正通过显式图结构运行时，才接近完整的 Graph Engineering。

---

## 4. DeepCode 当前能力审计

### 4.1 审计与验证范围

本次审计覆盖：

- Agent 内部工具循环。
- AgentSession。
- Goal Domain 与 GoalCoordinator。
- Goal Evaluator 与 TestService。
- Turn 持久化与 Event Projection。
- Context compaction。
- Sub-Agent 与 Worktree。
- Paper2Code Workflow。
- Automation。
- 重启恢复。

运行了以下相关测试集合：

- Goal Domain、Goal Store、Goal Service。
- Goal Evaluator、Goal Coordinator。
- AgentSession。
- Session Compaction。
- Spawn Agent。
- Team Worktree。
- Workflow Service。
- Automation Service。
- Verification。
- Turn Projection。

测试结果：

```text
83 passed in 5.40s
```

这不是全仓库测试结果，但覆盖了本报告判断所涉及的主要后端机制。

### 4.2 当前能力矩阵

| 能力 | DeepCode 当前状态 | 判断 |
|---|---|---|
| Agent 工具内循环 | 完整 | 强 |
| 权限、审批、Hooks、Skills | 完整且共享内核 | 强 |
| 跨 Turn Goal 外循环 | 已实现 | 强 |
| Attempt 与预算管理 | 已实现 | 强 |
| 完成、继续、阻塞、暂停等终态 | 已实现 | 强 |
| 重启后避免盲目重放副作用 | 已实现 | 强 |
| 自动测试后再语义判断 | 已实现 | 中等 |
| 完成证据绑定源码版本 | 未实现 | 明显缺口 |
| 验收标准逐项绑定证据 | 未实现 | 明显缺口 |
| 结构化任务进度状态 | 部分实现 | 偏弱 |
| 可恢复的上下文对象 | 部分实现 | 偏弱 |
| 持久化 Sub-Agent 图 | 未实现 | 缺失 |
| 显式依赖图与节点调度器 | 未实现 | 缺失 |
| 多循环治理图 | 未实现 | 缺失 |

---

## 5. DeepCode 已经具备的强项

### 5.1 Agent 内部循环已经成熟

核心实现：

- [`core/agent_runtime/runner.py`](../core/agent_runtime/runner.py)
- [`core/events/session.py`](../core/events/session.py)

当前支持：

- 模型调用与工具循环。
- 并发工具执行。
- 权限和人工审批。
- Pre/Post Tool Hooks。
- Stop Hook。
- 消息注入。
- 最大迭代和超时。
- 上下文裁剪与摘要压缩。
- 工具结果持久化。
- Thinking/Reasoning 元数据。

CLI 和 Desktop 通过 AgentSession 使用同一套执行内核。这一层架构是正确的，不应为了实现 Graph Engineering 而推翻。

### 5.2 Goal 是真实的外部循环

核心实现：

- [`core/domain/goal.py`](../core/domain/goal.py)
- [`core/application/goal_service.py`](../core/application/goal_service.py)
- [`core/application/goal_coordinator.py`](../core/application/goal_coordinator.py)

Goal 当前已经具有：

- Objective。
- Acceptance criteria。
- Attempt。
- Token、时间和次数预算。
- Skills。
- Verification command。
- 独立 Evaluator 模型配置。
- Active、Paused、Blocked、Usage Limited、Budget Limited 和 Completed 等状态。

GoalCoordinator 会把每次 Goal Attempt 作为普通 Turn 执行。Turn 完成后运行 Evaluator；如果结论是 `continue`，则自动发起下一次 Attempt。

这已经符合 Loop Engineering 的核心外部循环结构。

### 5.3 安全恢复逻辑是正确的

相关实现：

- [`core/application/turn_service.py`](../core/application/turn_service.py)
- [`core/application/goal_coordinator.py`](../core/application/goal_coordinator.py)

当前策略包括：

- 普通安全队列可以在重启后恢复。
- 已经开始执行、可能产生副作用的活跃 Turn 会被中断。
- Goal 在异常重启后暂停，等待用户检查后明确恢复。
- 不会盲目重放可能已经执行过的写操作。

这比“应用一启动就重新跑最后一步”更安全。

### 5.4 权限、Skills、Hooks 和事件持久化已经形成 Harness 基础

DeepCode 已具有：

- Permission Mode。
- Workspace fence。
- Approval。
- Turn 级 Skill immutable snapshot。
- Hook 生命周期。
- Tool allowlist。
- Typed events。
- SQLite Projection。
- Canonical Session JSONL。

这些能力已经构成良好的 Loop/Graph 底层控制平面。

### 5.5 Multi-Agent 已经有可用执行能力

核心实现：

- [`core/harness/agents/control.py`](../core/harness/agents/control.py)
- [`core/harness/tools/spawn_agent.py`](../core/harness/tools/spawn_agent.py)
- [`core/team/worktree.py`](../core/team/worktree.py)

当前支持：

- 最多五个并行 Sub-Agent。
- 非阻塞 Spawn。
- Mailbox。
- Wait、Interrupt、Send message。
- 对话历史 Fork。
- Git Worktree 隔离。
- 三方合并和冲突检测。

这是一套有效的多 Agent Harness 原语。

---

## 6. DeepCode 当前最关键的缺口

### 6.1 完成证据没有绑定当前源码状态

相关实现：

- [`core/application/goal_evaluator.py`](../core/application/goal_evaluator.py)
- [`core/application/test_service.py`](../core/application/test_service.py)
- [`core/verification.py`](../core/verification.py)

目前已经做到：

- 确定性测试失败时，LLM Evaluator 不能覆盖测试结果。
- 测试通过后，再进行语义完成判断。
- 可以为 Goal 指定不同 Evaluator 模型。

但是当前 TestResult 主要记录：

- 命令。
- 退出码。
- 输出。
- 超时状态。
- 执行时长。

没有记录：

- 测试对应的 Git HEAD。
- Dirty tracked diff hash。
- Untracked file hash。
- Verifier 配置版本。
- 验证完成后源码是否再次变化。

因此可能发生：

```text
测试通过
→ Agent 又修改代码
→ 旧 TestResult 仍然被当作当前证据
```

这是当前 DeepCode 可靠性方面最优先需要解决的问题。

### 6.2 验收标准没有逐项绑定证据

当前 Goal 保存 Acceptance Criteria，但系统没有明确表达：

```text
Criterion A
  → Evidence 1
  → Evidence 2

Criterion B
  → Evidence 3
```

Evaluator 主要读取当前 Attempt 的 Assistant 最终回答，以及 TestResult、FileChange、Diff、Error、Completion 等 Item 的摘要。

结果是：

- 系统知道“有一些证据”。
- 但不知道“哪条验收标准由哪份证据满足”。
- 也无法稳定解释为什么 Goal 可以完成。

### 6.3 Goal 进度状态仍然过度依赖文本

当前下一轮主要重新注入：

- Objective。
- Acceptance criteria。
- 上一轮 Evaluator reason。

没有形成明确的权威状态：

- 哪些 Criterion 已满足。
- 哪些 Artifact 已完成。
- 哪些 Evidence 仍然新鲜。
- 哪些测试仍然失败。
- 当前 Source revision。
- 哪些 Blocker 尚未解决。

当前 Stall Detection 主要判断连续若干次 Evaluator 的 `reason` 字符串是否相同。

这会产生两个问题：

- 实际没有进展，但 Agent 换了一种措辞，系统认为没有停滞。
- 实际有源码进展，但 Evaluator 使用相同描述，系统可能误判停滞。

### 6.4 Context Compaction 仍然是文本摘要

相关实现：

- [`core/agent_runtime/runner.py`](../core/agent_runtime/runner.py)

DeepCode 已经支持：

- Context budget。
- Micro-compaction。
- Tool result budget。
- Snip history。
- 模型生成 handoff summary。

但压缩结果仍然主要是一段文本，会损失：

- 任务和文件的关系。
- 工具结果的来源关系。
- Evidence 对应哪个 Criterion。
- 某个结论由哪次验证支持。
- 某个 Blocker 从哪次失败产生。

Runner 已经预留 `checkpoint_callback`，但 AgentSession 当前没有将其接到持久化层。因此重启时可以安全中断 Turn，但不能从模型回复、已完成工具和待执行工具的精确位置恢复。

### 6.5 Sub-Agent 不是持久化工作图

AgentControl 当前是进程内对象，其状态包括：

- Sub-Agent registry。
- Status。
- Mailbox。
- Inbox。
- Background task handle。

这些状态没有成为持久化领域对象，因此：

- 应用重启后无法恢复 Agent 图。
- Agent 与 Task 的关系不可长期审计。
- 没有显式 Task dependency。
- 没有 READY/BLOCKED 计算。
- 没有 Reviewer/Verifier 节点契约。
- 没有结构化结果聚合。
- Worktree merge 没有形成来源绑定的 Evidence Receipt。

### 6.6 Goal、Sub-Agent、Workflow、Automation 尚未统一

目前存在三个相邻的编排平面：

```text
普通 Turn / Goal
Sub-Agent AgentControl
Paper2Code Workflow
```

另外 Automation 负责定时触发普通 Turn。

相关实现：

- [`core/application/workflow_service.py`](../core/application/workflow_service.py)
- [`core/application/workflow_adapter.py`](../core/application/workflow_adapter.py)
- [`core/application/automation_service.py`](../core/application/automation_service.py)

Paper2Code Workflow 具有自己的：

- Stage。
- Checkpoint。
- Artifact。
- Retry。
- Human interaction。

但当前只支持固定的 `paper2code` 类型。

Automation 创建 Goal Mode Thread，但实际调用普通 `TurnService.start()`，并没有直接创建和运行 GoalCoordinator 的多 Attempt 外循环。

因此 DeepCode 已经拥有许多 Work Graph 原语，但还没有统一的权威 Work Graph。

---

## 7. 推荐优化方案

### P0：Evidence Contract

第一步不要先开发 Graph UI，而应增加两个核心领域对象：

```text
WorkspaceRevision
EvidenceReceipt
```

#### WorkspaceRevision

至少包含：

- Git HEAD。
- Tracked dirty diff hash。
- Untracked file manifest/content hash。
- 相关 DeepCode 配置 hash。
- Verifier profile hash。

仅使用 Git commit hash 不够，因为用户工作区可能存在未提交修改和未跟踪文件。

#### EvidenceReceipt

至少包含：

- Goal ID。
- Attempt ID。
- Criterion ID。
- WorkspaceRevision。
- Verification profile。
- 命令及其配置 hash。
- Exit code。
- Duration。
- Output digest。
- 原始结果 Artifact reference。
- Created time。

系统规则必须是：

> 当前 WorkspaceRevision 与 EvidenceReceipt 不一致时，证据立即变为 stale，不能支持 Goal 完成。

#### 配置驱动的 VerifierProfile

验证器不应通过不断增加硬编码条件扩展，而应使用配置驱动：

```yaml
verification:
  profiles:
    - id: unit
      command: ["pytest", "-q"]
    - id: lint
      command: ["ruff", "check", "."]
    - id: typecheck
      command: ["mypy", "core"]
```

系统内仍要保留：

- Allowlist。
- Workspace fence。
- Timeout。
- Bounded output。
- Permission policy。

### P1：结构化 GoalState

增加由类型化事件归约产生的权威状态：

```text
GoalState
├── criteria
│   ├── pending
│   ├── satisfied
│   └── blocked
├── artifacts
├── blockers
├── source_revision
├── evidence_refs
├── active_tasks
└── progress_fingerprint
```

核心原则：

- Transcript 是供用户阅读的记录，不是系统状态。
- LLM 输出是状态变更提议，不是权威状态。
- 状态只能通过类型化事件更新。
- 下一轮 Context 从 GoalState 构建。
- Completion 必须由 GoalState 和 Evidence Gate 决定。

Progress fingerprint 应至少考虑：

- WorkspaceRevision 是否变化。
- Criterion 状态是否变化。
- 测试失败集合是否变化。
- Evidence 是否增加或失效。
- Artifact 是否变化。
- Blocker 是否变化。

这样可以替换目前的自然语言 reason 字符串停滞检测。

### P2：最小持久 Work Graph

不要重写现有 Turn、Goal 和 Session，也不要创建 Desktop 专用编排器。

第一版只需要少量稳定节点类型：

```text
WorkNode:
  goal | task | turn | agent | artifact | evidence | approval
```

以及少量稳定边类型：

```text
WorkEdge:
  depends_on
  produced
  verified_by
  blocked_by
  delegated_to
  derived_from
  supersedes
```

可以继续使用现有 SQLite 和 EventRepository，不需要立即引入图数据库。

#### 确定性调度规则

```text
所有 depends_on 已完成
+ 权限条件满足
+ 并发资源可用
= READY
```

LLM 可以提出 Task、Dependency 或 Delegation 建议，但最终状态转移由确定性调度器执行。

#### 接入顺序

1. `update_plan` 创建或更新 Task Node，不再只写 `turn.plan.updated`。
2. Sub-Agent 变成持久化 Agent/Task Node。
3. Worktree merge 产生 Artifact 和 Evidence Node。
4. GoalCoordinator 根据 Work Graph 状态继续任务。
5. Paper2Code Workflow 作为一种 Graph Template 接入。
6. Automation 触发 Goal/Graph，而不只是普通 Turn。

### P3：安全 Checkpoint 与结构化 Context

将现有 `checkpoint_callback` 接入持久化层，但只能在安全边界写入：

- 模型回复已经保存，工具尚未执行。
- 一批工具已经全部结束。
- 状态事件已经成功提交。
- Verification Receipt 已经生成。

写工具需要 Effect/Idempotency Receipt：

- 已确认完成：不能重复执行。
- 已确认未执行：可以重试。
- 执行结果未知：暂停并请求用户检查。

Context Builder 应按照当前 Work Node 提取相关子图：

- Goal。
- 当前 Task。
- 上游依赖。
- 相关 Artifact。
- 相关 Evidence。
- 当前 Permission。
- 当前 Blocker。

原始工具输出保留在可恢复 Sidecar 中，Context 中只放引用和必要摘要。

### P4：Improvement/Governance Graph

这一阶段才进入完整 Graph Engineering：

- 节点级成功率、延迟、成本、重试和回归。
- 主指标与 Counter-metric。
- 冻结需求、隐藏测试、权限和预算 Anchor。
- Implementer 与 Evaluator 可使用不同模型、工具和权限。
- Agent 不能修改自己的 Verifier、Stop Rule、Budget 或 Anchor。
- LoopSpec 与 GraphSpec 版本化。
- Canary rollout。
- Rollback。
- Verifier drift 监控。
- Goodhart 行为检测。

---

## 8. 产品差异化建议

不建议将 DeepCode 定位为：

> 另一个支持多 Agent 的 Coding Agent。

推荐定位：

> **DeepCode 不只生成代码，而是持续维护一张从目标、任务、变更、验证到证据的深层工作图。**

核心产品路径：

```text
Intent
  → Plan
  → Work
  → Change
  → Verification
  → Evidence
  → Decision
```

用户最终能够看到：

- 当前目标是什么。
- 哪些任务已经完成。
- 哪些 Agent 完成了哪些工作。
- 哪些文件发生了改变。
- 每条验收标准由什么证据支持。
- 证据是否仍对应当前源码。
- 系统为什么继续、阻塞或完成。
- 应用崩溃后从哪里安全恢复。

普通任务仍然使用单 Agent Loop。只有存在以下条件时才展开 Work Graph：

- 真实并行任务。
- 不同权限边界。
- 独立验证需求。
- 不同失败域。
- 需要人工 Gate。
- 需要明确依赖和结果聚合。

这样可以避免为了 Graph 而增加不必要的延迟、Token 成本和工程复杂度。

---

## 9. 不建议采用的方案

- 不要用 LangGraph 或其他框架重写现有 AgentSession。
- 不要创建 Desktop 专用的另一套 Graph 后端。
- 不要硬编码 Agent 角色、模型名称或供应商。
- 不要让 LLM 单独决定 `DONE`。
- 不要先做 Graph 可视化，再补领域语义。
- 不要默认启动大量 Sub-Agent。
- 不要让 Agent 修改自己的验证器、权限、预算和终止条件。
- 不要把所有 Transcript Item 都机械地变成 Graph Node。
- 不要让 Paper2Code、Goal 和 Automation 分别继续发展为彼此重复的编排系统。

---

## 10. 最终判断与实施顺序

DeepCode 已经拥有优秀的 Loop Engineering 底座，尤其是：

- 共享 AgentSession。
- Goal 外循环。
- 权限和审批。
- Hooks。
- Skills。
- 持久 Session。
- Typed events。
- Worktree 隔离。
- 安全恢复。

但是，DeepCode 要从 Coding Agent 中真正脱颖而出，下一步不应该优先堆叠更多 Agent，也不应该优先做 Graph UI。

正确顺序是：

```text
P0  源码绑定证据
  → P1  结构化 GoalState
  → P2  持久 Work Graph
  → P3  Graph 调度、Checkpoint 与结构化 Context
  → P4  多循环治理
```

其中最优先的是 **P0 Evidence Contract**。

没有可靠证据的图只会更高效地放大错误。一旦证据层建立起来，DeepCode 现有的 Goal、Turn、Workflow、Sub-Agent、Automation、CLI 和 Desktop 都可以自然汇入同一套产品架构，而不需要破坏现有 CLI 与 Agent 核心逻辑。
