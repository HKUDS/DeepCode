# DeepCode Loop Engineering：Codex 对齐实施计划

> 状态：**唯一有效的 Loop Engineering 计划**
>
> 基线日期：2026-07-27
>
> 适用端：DeepCode CLI、DeepCode Desktop、App Server
>
> Codex 源码基线：`openai/codex@61a44880a85d2fd0d8770908dea5733495e571c8`
>
> 当前进度：P0–P5 核心架构已完成并通过确定性门禁与真实模型验收；
> P6.1–P6.7 已实施并完成全量、clean install 与真实跨端验收。Loop
> Engineering 的当前计划已产品级收口。

## 1. 最终架构决议

DeepCode 只保留一个 Agent 执行循环：

```text
Thread
  ├─ ordinary Turns
  │    ├─ start
  │    ├─ steer
  │    ├─ interrupt
  │    └─ explicit queue
  └─ optional ThreadGoal extension
       ├─ persistent objective
       ├─ turn attribution
       ├─ goal tools
       └─ normal-idle continuation
```

新的 Loop Engineering 不再建设第二套任务编排系统。CLI、Desktop 和
Headless 调用同一个 Application、ThreadRuntime、SessionStore 和
GoalExtension。

以下旧设计正式淘汰：

- GoalAttempt、GoalEvaluation、GoalDecision 组成的第二套执行账本；
- GoalCoordinator 在普通 Turn 之外创建和结算 Goal Attempt；
- 每一轮默认再调用一个 LLM evaluator；
- 根据用户文本中的“停止”“修改目标”“继续”等关键词路由控制操作；
- `turn/steer` 失败后由后端静默转成 Queue；
- Stop Turn 自动 Pause Goal；
- Stop Turn 写入 continuation deferral 或额外 hold 状态；
- Goal Edit 生成隐藏 revision，并让旧 revision 的子代理自动失效；
- Goal Edit 自动广播或自动中断所有子代理；
- CLI Goal 功能另开第二个 `DeepCodeApplication`；
- 用固定文件名、语言或测试命令判断任意 coding Goal 是否完成。

这不是删除原来的 Session 机制。Session ID、任意目录启动、跨目录恢复、
canonical history、Skills、权限、Sandbox、模型和 thinking 配置都继续保留。

---

## 2. Codex 源码基线

本计划参考的是本地 Codex 源码的实际控制流，不是对 Codex Desktop 外观的
猜测。

### 2.1 同一输入框只按运行状态路由

Codex TUI 的发送算法位于
`codex-rs/tui/src/app/thread_routing.rs:644`：

```text
有 active Turn
  → turn/steer(expectedTurnId, originalInput)

没有 active Turn
  → turn/start(originalInput)
```

`NoActiveTurn` 时，相同输入回退为 `turn/start`；Turn ID 发生竞态时，客户端
使用服务端返回的实际 ID 最多重试一次。

宿主只判断输入应该投递到哪个 Turn，不判断输入在语义上想做什么。用户输入的
“先别写”“换一种方案”“继续检查”全部由主 Agent LLM 原样理解。

### 2.2 Queue 是投递时机

Codex 的 Enter 和 Queue 使用不同交互路径：

- active 时 Enter 默认 Steer 当前 Turn；
- 显式 Queue 才表示“在下一 Turn 处理”；
- Queue 使用 FIFO；
- 一个 Turn 结束后只启动一条 queued input。

相关源码：

- `codex-rs/tui/src/bottom_pane/chat_composer.rs:3342`
- `codex-rs/tui/src/chatwidget/input_flow.rs:96`

DeepCode 可以继续使用 durable Queue，但 Queue 不是一种任务语义，也不能成为
Steer 的隐藏 fallback。

### 2.3 Interrupt 与正常完成是两条路径

Codex 的 `turn/interrupt` 精确校验 `(threadId, turnId)`。中断会取消当前
task，并在终止事件之前持久化 model-visible interruption marker：

- `codex-rs/app-server/src/request_processors/turn_processor.rs:1416`
- `codex-rs/core/src/session/mod.rs:4061`
- `codex-rs/core/src/tasks/mod.rs:868`

Goal Extension 的 `on_turn_abort` 只结算本 Turn，不 Pause Goal：

- `codex-rs/ext/goal/src/extension.rs:280`

因此：

```text
Interrupt Goal-associated Turn
  → 当前 Turn interrupted
  → Goal 仍保持原状态
  → 不立即自动启动 Goal continuation
  → 下一条用户输入创建普通 Turn
  → 新 Turn 重新关联仍 active 的 Goal
```

Codex 的 continuation deferral 用于携带 Goal 的 Thread Fork，不是 Stop 机制。
DeepCode 在没有实现 Goal Fork 继承前不引入 deferral。

### 2.4 Goal 是 Thread Extension

Codex Goal Extension 在普通 Turn 生命周期上工作：

- Turn start 时关联 active Goal：
  `codex-rs/ext/goal/src/extension.rs:195`
- 正常 idle 时尝试 continuation：
  `codex-rs/ext/goal/src/runtime.rs:359`
- 运行中修改 objective 时注入结构化 steering：
  `codex-rs/ext/goal/src/runtime.rs:189`
- 模型只能把 Goal 标记为 complete 或 blocked：
  `codex-rs/ext/goal/src/tool.rs:221`

Goal Edit 保持同一个 Goal 身份。Codex 的
`state/src/runtime/goals.rs:271` 会更新 objective，但不会把每次编辑变成
新的 Goal ID。

这意味着 active Turn 收到 Goal Update 后可以继续工作并完成修改后的 Goal；
不需要隐藏 revision、Attempt fence 或子代理 merge fence。

### 2.5 本计划有意保留的 DeepCode 差异

DeepCode 不复制 Codex 的全部复杂度：

- Core `turn/start` 保持 strict start；active 时返回 typed error，不采用底层
  replace-active-task 语义；
- 暂不实现 Goal Fork inheritance、continuation deferral 和完整 analytics；
- 暂不引入 UsageLimited Goal 状态；provider 限流作为 typed execution error；
- 保留 DeepCode 现有 JSONL Session ledger 与 SQLite projection，不迁移到
  Codex 的数据库实现；
- 保留显式 durable Queue，确保 CLI/Desktop 跨进程一致。

这些差异不会改变 Codex 式的交互和生命周期。

---

## 3. 产品行为契约

### 3.1 用户操作

| 用户操作 | 当前状态 | 系统行为 | Goal objective |
|---|---|---|---|
| Enter | active Turn | Steer 精确 Turn | 不变 |
| Enter | idle | Start 下一普通 Turn | 不变 |
| Queue | 任意 | 显式保存为后续 Turn | 不变 |
| Stop | active Turn | Interrupt 精确 Turn | 不变 |
| Edit Goal | 任意 | 更新持久 objective | 显式改变 |
| Pause Goal | 任意 | 禁止自动 continuation | 不变 |
| Resume Goal | paused/blocked | 恢复 active | 不变 |
| Clear Goal | 任意 | 删除 Thread 当前 Goal | 删除 |

普通 follow-up 可以改变 Agent 接下来的做法，但不会偷偷改写持久 Goal。

### 3.2 Stop 后继续

```text
Stop Turn A
  → 取消 A 的模型、工具、审批等待和本 Turn 仍拥有的执行
  → 持久化 interruption marker
  → Session 历史与已经完成的修改保留
  → 用户输入 B
  → 在同一 Thread 启动 Turn B
  → B 看到完整历史和 A 被中断的事实
```

如果 Thread 存在 active Goal，Turn B 自动关联该 Goal。Stop 本身不会 Pause、
Block、Edit 或 Clear Goal。

### 3.3 Queue 与 Goal continuation 的优先级

同一 Thread 进入 idle 时按以下顺序处理：

1. 已经接受的 active-turn inbox 输入；
2. 用户显式 Queue；
3. Goal 自动 continuation；
4. 保持 idle。

Goal 不能越过用户已经排队的指令。

---

## 4. 宿主与 LLM 的职责边界

### 4.1 宿主必须硬编码的通用协议

这些是产品一致性，不是任务语义：

- 一个 Thread 最多一个 active Turn；
- `expectedTurnId`、`clientMessageId` 和 Goal ID 的一致性；
- active/idle、completed/interrupted/failed 生命周期；
- 输入大小、队列容量、权限、Sandbox、审批和预算；
- 持久化、幂等、CAS 和事件发布顺序；
- Stop 精确取消哪个 Turn；
- Goal tool 只能来自关联该 Goal 的 active Turn；
- 模型只能请求 complete/blocked。

### 4.2 必须交给 LLM 的判断

- 用户自然语言是在纠正、补充、改变方法还是要求继续；
- coding 任务需要运行哪些测试、Build、Lint 或人工检查；
- 当前证据是否足以请求 Goal complete；
- 阻塞原因是否真实存在；
- 是否需要联系、Steer 或重新派发某个子代理；
- 某个子代理结果在最新上下文中是否仍然有用。

系统不实现 `VerificationPolicy(task_type)`。Agent 通过正常工具读取代码、执行
测试并观察结果；只有合法的 `update_goal(complete)` 才改变 Goal 状态。

---

## 5. 目标架构

```text
CLI adapter ───────────┐
                       │
Desktop App Server ────┼──▶ InteractiveTurnRouter
                       │             │
Headless adapter ──────┘             ▼
                              shared ThreadRuntime
                                  │       │
                                  │       ├─ typed inbox
                                  │       ├─ interrupt boundary
                                  │       └─ lifecycle events
                                  ▼
                            AgentSession / AgentRunner
                                  │
                                  └─ tools / approvals / sandbox / agents

GoalExtension ── consumes Thread lifecycle
  ├─ ThreadGoalStore
  ├─ Turn goal attribution
  ├─ get_goal / update_goal
  └─ normal-idle continuation

SessionStore ── canonical Session identity/history/workspace
SQLite ──────── Turn/Item projection and replay
```

### 5.1 ThreadRuntime 是唯一执行真相

共享 runtime 只暴露严格原语：

```text
start_turn(input, client_message_id, execution_snapshot)
steer_turn(expected_turn_id, input, client_message_id)
interrupt_turn(expected_turn_id)
enqueue_turn(input, client_message_id, execution_snapshot)
try_start_turn_if_idle(input, execution_snapshot)
```

它不知道 Goal objective 的含义，也不知道调用端是 CLI 还是 Desktop。

### 5.2 Typed active-turn inbox

运行中注入使用判别类型：

```text
UserSteer
GoalObjectiveUpdated
SubagentMessage
```

三种输入共享有界队列和 drain 安全点，但不共享语义：

- UserSteer 是 durable user history；
- GoalObjectiveUpdated 的权威来源是 GoalStore；
- SubagentMessage 的权威来源是 AgentControl；
- 每项严格绑定 target Turn；
- 不允许 carry-forward 到未知的未来 Turn；
- ACK 的 UserSteer 必须保证当前 Turn 至少再进行一次模型采样。

inbox 生命周期保持：

```text
STARTING → OPEN → CLOSING → CLOSED
```

Steer 和关闭检查使用同一并发边界。CLOSING/CLOSED 后不再返回虚假 accepted。

### 5.3 ThreadGoal 最小模型

```text
ThreadGoal {
  thread_id
  goal_id
  objective
  status
  token_budget?
  tokens_used
  time_used_seconds
  created_at
  updated_at
}
```

状态只保留：

```text
active
paused
blocked
budget_limited
complete
```

关键语义：

- `goal_id` 标识一个 Thread Goal 生命周期；
- objective edit 保持同一 `goal_id`；
- clear 后新建才生成新 `goal_id`；
- Turn start 记录当时关联的 `goal_id`；
- 不再维护 runtime definition revision、GoalAttempt 或 GoalDecision；
- Goal 进度由 Goal snapshot 和关联 Turns 派生。

### 5.4 Goal 并发与编辑

所有 Goal mutation 和 idle continuation 使用同一个 per-thread Goal mutation
边界：

```text
lock thread goal state
  → read current Goal
  → validate expectedGoalId
  → write one complete transition
  → update runtime accounting
  → inject active Turn or try-start-if-idle
unlock
```

外部 API 的 Edit/Pause/Resume/Clear 携带 `expectedGoalId`，用于防止旧客户端
覆盖另一个客户端刚完成的 Clear 或新建。

运行中 Edit：

1. 持久化新 objective；
2. 保持同一个 `goal_id`；
3. 注入 typed `GoalObjectiveUpdated`；
4. 当前 Turn 继续在最新 objective 上工作。

idle Edit：

1. 持久化新 objective；
2. `try_start_turn_if_idle`；
3. 如果同时出现用户 Queue，Queue 优先。

### 5.5 Goal tools

Goal-associated Turn 只增加两个工具：

```text
get_goal()
update_goal(status = complete | blocked, reason?)
```

模型不传 `thread_id`、`turn_id`、`goal_id` 或 revision。它们来自不可伪造的
ToolInvocation runtime context。

宿主验证：

- invocation 的 Turn 仍属于该 Thread；
- runtime context 的 Goal ID 仍是 Thread 当前 Goal；
- Goal 状态允许本次 transition；
- update status 只能是 complete 或 blocked。

宿主不验证任务类型，不运行第二个 LLM evaluator。

### 5.6 Goal 生命周期

```text
Turn start
  → 若 Goal active，则关联 goal_id 并开放 Goal tools

Turn completed
  → 结算 token/time
  → 若 Goal 仍 active，等待 thread idle
  → 用户 Queue 为空时才可启动 continuation

Turn interrupted
  → 结算已经发生的 usage
  → Goal 状态不变
  → 不立即 continuation

Turn terminal error
  → 结算 usage
  → 不自动重试
  → Goal 置 blocked，并记录通用 runtime error reason

下一用户 Turn
  → 若 Goal active，则自动关联

Goal Edit
  → 同 ID 更新 objective
  → active 时 typed injection
  → idle 时 try-start-if-idle
```

Terminal error→blocked 对齐 Codex
`ext/goal/src/runtime.rs:246` 的防消耗循环策略。它只表达“自动执行不能安全
继续”，不是对 coding 任务完成度的硬编码。用户可以 Resume 后继续。

---

## 6. 持久化与迁移

### 6.1 单一 writer

新 GoalStore 每个 mutation 只写一个完整 v2 transition：

```text
thread_goal.snapshot.v2
thread_goal.cleared.v2
```

每条 transition 包含恢复当前 Goal 所需的完整 snapshot，并在 Session guard
内 append、flush、fsync。禁止把一次状态变化拆成 Decision、Evaluation、
Attempt、Goal 四次写入。

### 6.2 Dual-read / single-write

- reader 可以读取现有 v1 Goal、revision、Attempt、Evaluation 和 Decision；
- legacy 数据只折叠成当前 ThreadGoal 和只读历史摘要；
- 第一次新 mutation 后只追加 v2；
- 不 dual-write 旧格式；
- 不批量改写 Session JSONL；
- legacy decoder 独立放置，不能让旧 domain 继续进入生产 runtime。

SQLite 仍是可重建 projection，JSONL Session ledger 仍是持久事实来源。

---

## 7. 文件边界

只新增三个有单一职责的核心模块：

```text
core/domain/thread_goal.py
  → 最小 ThreadGoal 与状态转换

core/sessions/thread_goal_store.py
  → v2 fold、CAS、single-write

core/application/goal_extension.py
  → Turn lifecycle、tool handler、idle continuation
```

迁移期允许一个纯 decoder：

```text
core/sessions/legacy_goal_decoder.py
  → 只读 v1 ledger，不写、不调度
```

继续复用：

- `core/application/turn_service.py`
- `core/application/interactive_turn_router.py`
- `core/application/session_runtime.py`
- `core/agent_runtime/goal_runtime.py`
- `core/harness/tools/goal.py`
- App Server 和 CLI/Desktop adapter

必须删除或收缩：

- `core/application/goal_coordinator.py`
- `core/application/goal_evaluator.py`
- 旧 `GoalAttempt`、`GoalEvaluation`、`GoalDecision` domain；
- `Turn.goal_definition_revision` 和 `Turn.goal_attempt_id` runtime 使用；
- `start_goal_attempt`；
- CLI Goal 第二 Application 路径；
- Desktop Attempt/Evaluation/Decision UI；
- 旧 writer、旧 prompts、dead config 和只验证旧实现的 tests。

`TurnService` 不再承载 Goal 业务分支。它只通过一个小的 lifecycle port
获取 Turn attribution，并发布 settled event。

---

## 8. 分阶段执行

每个 P 都必须先增加失败测试，再实现，再通过真实门禁。一个 P 没有完整验收
证据，不得标记完成，也不得开始下一个 P。

### P0：Contract Freeze（已完成）

目标：冻结稳定产品能力和错误语义，不改 Session 数据。

已完成：

- 普通 CLI/Desktop、Session、模型/effort、Skills、审批和 Goal ledger fixtures；
- 任意目录启动与跨目录恢复 characterization；
- 明确关键词不触发宿主控制；
- 冻结 strict Turn 和 Goal migration 边界。

验收结果已纳入 P2 总门禁。

### P1：Strict Turn Control（已完成）

目标：让普通 Turn 具备 Codex 式 start/steer/interrupt。

已完成：

- strict `turn/start`；
- `turn/steer(expectedTurnId)`；
- typed race errors 与同一 `clientMessageId` 幂等；
- typed inbox 与 `STARTING→OPEN→CLOSING→CLOSED`；
- 删除 implicit Queue、carry-forward 和固定 injection cycle；
- interruption marker、精确 Stop 和统一 active Turn 查询。

### P2：CLI / Desktop Runtime Parity（已完成）

目标：普通无 Goal 任务也能在同一 Session 中实时纠正、停止和继续。

已完成：

- CLI/Desktop 共享 active/idle input router；
- 最多一次竞态恢复；
- 显式 Queue；
- CLI 单 Application runtime；
- Stop 后同 Session 继续；
- 跨端恢复和任意目录启动保持兼容。

真实门禁：

- Python：`707 passed`；
- Desktop Vitest：`68 passed`；
- protocol codegen、TypeScript、ESLint、production build：通过；
- Tauri/Rust fmt、Clippy `-D warnings`、tests：通过；
- `pre-commit run --all-files`：通过；
- 仓库外目录执行已安装 `deepcode` 并创建 canonical Session：通过。

### P3：GoalExtension v2 Core（已完成）

目标：在唯一普通 Turn loop 上完成 Goal v2 的生产切换。

实施顺序：

1. 为 ThreadGoal、状态转换、Goal ID CAS 写 domain tests；
2. 实现 v2 store 和 v1 fixture decoder；
3. 将普通 Turn start 自动关联 active Goal；
4. 简化 GoalRuntimeContext，只保留 Thread/Turn/Goal 身份；
5. 将 `get_goal` / `update_goal` 改成无 revision 参数；
6. 实现 normal-complete continuation；
7. 实现 interrupt-no-continuation；
8. 实现 terminal-error→blocked；
9. 实现 active/idle Goal Edit；
10. 将 Application production wiring 从 GoalCoordinator 切到 GoalExtension；
11. 更新 App Server、CLI `/goal` 和 Desktop GoalRail；
12. 确认新运行只写 v2 transition。

P3 验收结果：

- 普通 follow-up 改方向但不改 objective；
- active Goal Edit 保持 Goal ID，当前 Turn 收到 typed update；
- idle Goal Edit 只启动一个 continuation；
- stale Goal ID 不能覆盖 clear 后的新 Goal；
- Agent tool 不接受 thread/turn/goal/revision 参数；
- 模型只能 complete/blocked；
- normal completion 才自动 continuation；
- Interrupt 后 Goal 状态不变且不立即续跑；
- 下一用户 Turn 重新关联 active Goal；
- terminal error block Goal，用户 Resume 后可继续；
- Queue 永远优先于 Goal continuation；
- 新 ledger 不含 Attempt/Evaluation/Decision；
- CLI/Desktop 使用同一 Goal 行为。

阶段门禁已通过：

- Goal domain/store migration、Turn/Goal lifecycle race、App Server contract、
  CLI/Desktop Goal interaction 测试；
- Python、Desktop、Rust 全量门禁；
- protocol codegen、TypeScript、ESLint、production build；
- `pre-commit run --all-files` 与 `git diff --check`。

### P4：Legacy Deletion（已完成）

目标：净删除第二套 Goal loop，确保生产图中只剩 GoalExtension。

实施顺序：

1. 删除 Application 对 GoalCoordinator/GoalEvaluator 的构造和 shutdown；
2. 删除 `start_goal_attempt` 和 Goal Attempt continuation；
3. 删除 GoalAttempt/Evaluation/Decision writer 与 runtime domain；
4. 删除默认 semantic evaluator 和 verifier dispatch；
5. 删除旧 Goal prompts、dead config、views 和 protocol 字段；
6. 删除 CLI/Desktop legacy adapter 与 UI；
7. 把必要 v1 兼容收缩到纯 `legacy_goal_decoder.py`；
8. 用静态依赖检查确认生产代码不再 import legacy；
9. 记录净删除行数和剩余兼容边界。

P4 验收结果：

- 真实旧 ledger fixture 可无损读取；
- 新运行永不写 legacy event；
- production import graph 不含 Coordinator/Evaluator/Attempt；
- 删除 projection 后可以从 Session ledger 重建；
- active Goal 与普通 Session 均可跨重启恢复；
- Goal 相关生产代码显著净减少；
- Python 全量 `689 passed`；
- Desktop `68 passed`，ESLint、TypeScript、production build 通过；
- Rust fmt、Clippy `-D warnings`、tests 通过；
- `pre-commit run --all-files` 与 `git diff --check` 通过；
- legacy core 净删除约 2,985 行，生产图只保留只读 v1 decoder。

### P5：Recovery and Product Release（已完成）

目标：验证长期运行、崩溃、跨端和真实模型边界。

实施顺序：

1. fault injection 验证 Goal transition 原子性；
2. 验证 crash 不自动重放未知副作用；
3. 验证 Queue、Goal continuation 和用户新输入的优先级；
4. 验证模型/provider/thinking 切换只影响下一 Turn；
5. 验证 CLI 发起、Desktop 恢复，以及反向恢复；
6. 验证 Stop terminal 后旧 Turn 不再产生新的自动集成副作用；
7. 验证 User Steer 只进入主 Agent，子代理由主 Agent LLM 显式协调；
8. 更新 README、CLI help、Desktop empty states 和迁移说明；
9. 在隔离 SessionStore 中执行一次真实 LLM CLI/Desktop smoke；
10. 在 clean checkout 运行最终发布门禁。

P5 最终门禁：

- Python 全量 tests；
- App Server schema/codegen；
- Desktop tests、ESLint、TypeScript、production build；
- Tauri fmt、Clippy `-D warnings`、tests；
- `pre-commit run --all-files`；
- clean checkout 任意目录 CLI 启动；
- Desktop 恢复同一 Session；
- 真实 API Goal：create→work→steer→interrupt→resume→complete；
- 原 Session ID、workspace 和 history 未被改写。

P5 验收结果：

- Goal JSONL 以换行为原子提交边界；撕裂尾部可恢复，完整坏记录仍 fail-closed；
- 故障注入、并发累计、崩溃不重放、Queue 优先级、Stop 和 mailbox 隔离通过；
- provider/model/thinking 只影响下一 Turn；Goal ID 与 Session ID 保持稳定；
- CLI 创建、Desktop 编辑、CLI 再编辑、Desktop 再读取的双向恢复通过；
- OpenRouter `moonshotai/kimi-k3`（high thinking）真实执行
  `work → steer → interrupt → resume → complete`，最终文件字节、工作区边界与
  canonical history 均通过独立断言；
- Python 全量 `695 passed`；
- Desktop `68 passed`，ESLint、TypeScript、protocol check、production build
  通过；
- Rust fmt、Clippy `-D warnings`、tests 通过；
- `pre-commit run --all-files` 与 `git diff --check` 通过；
- 将 tracked diff 与全部新文件投影到 detached clean worktree 后，29 个关键
  Goal/恢复/跨端测试与仓库外 CLI help 启动通过。

### P6：Cross-Surface Product Closure（已完成）

目标：修复 P1 遗留的输入边界竞态，补齐 Goal 的运行状态表达、显式继续、
结果可审计性、跨端来源和 Headless 恢复入口。在不引入第二套 Agent Loop、
任务类型验证器或自然语言关键词路由的前提下，让 CLI、Desktop 和 Headless
达到一致的产品闭环。

#### P6.0 硬编码边界

允许固定的是通用协议和产品不变量：

- `STARTING / OPEN / CLOSING / CLOSED` 输入边界；
- `active / paused / blocked / budget_limited / complete` Goal 状态；
- 一个 Thread 最多一个 active Turn；
- `expectedTurnId`、`expectedGoalId`、`messageId` 的一致性；
- 有限且有明确上限的竞态恢复；
- 输入、reason 和展示证据的有界容量；
- `cli / desktop / headless / automation / app_server / internal`
  客户端来源枚举。

禁止固定的是任务语义：

- 不解析“继续”“停止”“换方案”等自然语言关键词来调用控制操作；
- 不根据语言、文件名、框架或固定测试命令判断 Goal 是否完成；
- 不根据错误消息字符串判断并发状态；
- 不把 App Server、CLI 或 Headless 偷偷当成 Desktop；
- 不增加 Goal evaluator、Goal coordinator 或另一套 Runner；
- 不把关联证据当成宿主的第二套完成判定。

#### P6.1 `CLOSING → next Turn` 输入竞态（已完成）

Codex 在同一 active-Turn 并发边界内完成 active 检查与输入接收，因此客户端
只会看到“Steer 已接受”或“No active Turn”。DeepCode 因为输入需要经过
`reserve → persist → commit`，内部保留 `CLOSING` 是合理的，但它不能成为
用户可见的消息失败。

统一交互路由必须遵守：

```text
submit message M with stable messageId
  → cached active Turn exists
      → steer(expectedTurnId, M)
          → accepted: done
          → expected mismatch: retry once with actual Turn
          → no active: start-or-steer(M)
          → closing/closed:
              wait for the exact Turn terminal boundary
              → start-or-steer(M)
  → no cached active Turn
      → start-or-steer(M)

start-or-steer(M)
  → start(M)
      → started: done
      → another Turn won the race: steer that actual Turn once
```

约束：

- 所有路径复用同一个 `messageId`；
- 不递归、不无限重试、不使用固定 sleep 轮询；
- 不把拒绝的 Steer 静默转为 Queue；
- 不把输入 carry-forward 到未知 Turn；
- `TurnService.start/steer/enqueue/interrupt` 继续保持严格原语；
- Python CLI router 与 Desktop RPC router 使用同一错误码和状态契约。

预计修改：

- `core/application/interactive_turn_router.py`
- `core/application/errors.py`
- `app_server/dispatcher.py`
- `desktop/src/app/interactiveTurnRouter.ts`
- 对应 Python、App Server 和 Vitest 契约测试

#### P6.2 Goal 派生运行状态（已完成）

`ThreadGoal.status` 继续只保存持久 Goal 生命周期。`working`、
`readyToContinue` 和 `finishing` 不写入 Goal ledger，由 Desktop 根据
Goal 与 Turn 的联合状态纯派生：

```text
Goal active + matching Turn executing
  → working

Goal active + no matching executing Turn
  → readyToContinue

Goal complete + deciding Turn not terminal
  → finishing

Goal complete + deciding Turn terminal
  → complete

paused / blocked / budget_limited
  → use persisted Goal status
```

实现一个独立纯函数，例如
`desktop/src/features/goal/goalPresentation.ts`。`GoalRail` 只渲染其结果，
不在 JSX 和 CSS 中散落交叉状态判断，也不新增数据库字段。

#### P6.3 显式 Continue（已完成）

新增明确的 `thread/goal/continue` 操作，不解析普通输入中的“继续”，也不把
active Goal 的 Continue 偷偷重命名成 Resume。

请求：

```json
{
  "threadId": "…",
  "expectedGoalId": "…"
}
```

响应：

```json
{
  "goal": {},
  "disposition": "started | alreadyRunning",
  "turnId": "…"
}
```

行为：

- `active + idle`：通过现有普通 `TurnService` 启动 Goal continuation；
- `active + running`：幂等返回 `alreadyRunning`，不重复启动；
- `paused / blocked / budget_limited`：继续使用显式 Resume；
- `complete`：必须由用户 Reopen/Edit，Continue 不修改状态；
- 必须校验 `expectedGoalId`，旧 UI 不得唤醒已经 Clear/替换的 Goal。

入口：

- CLI：`/goal continue`
- Desktop：仅在 `readyToContinue` 时显示 Continue
- Headless `--resume`：恢复 active-idle Goal 时调用同一操作

`GoalExtension.continue_if_idle` 仍是唯一 continuation 实现；不得创建新的
Goal runner。

#### P6.4 跨端消息来源（已完成）

新增 typed `ClientSurface`：

```text
cli
desktop
headless
automation
app_server
internal
```

严格区分：

```text
client
  → 谁提交：cli / desktop / headless / …

source
  → 如何投递：start / steer / queue / goal_continuation / …
```

来源必须由 adapter 或已初始化的 App Server connection 注入：

- CLI adapter → `cli`
- Desktop `clientInfo.surface` → `desktop`
- `deepcode loop` → `headless`
- Automation → `automation`
- Goal continuation → `internal`
- 未声明 surface 的通用 App Server → `app_server`

禁止根据 `clientInfo.name`、调用栈、Session 初始 kind 或自然语言猜来源。
同一 Session 可以依次由 CLI、Desktop 和 Headless 写入，每条用户消息保留
自己的 client；assistant 最终消息记录该 Turn 的启动来源。

这只修复消息 metadata，不修改 Session ID、Session 目录、workspace origin
或跨目录恢复机制。

#### P6.5 Goal outcome 与关联证据（已完成）

`ThreadGoal` 保持最小模型，新增只读结果投影：

```text
GoalOutcome {
  status
  reason
  source
  decidedByTurnId
  decidedAt
  evidenceRefs[]
}
```

现有 v2 Goal ledger 已记录 `reason`、`source`、`turnId` 和 Goal snapshot。
折叠器必须保留最近一次真正把 Goal 转为 `complete` 或 `blocked` 的决策，
后续 usage accounting snapshot 不得覆盖原始决策 reason。Runtime failure
应保存真实、已清理和有界的 `turn.error_message`，不能只持久化
`"turn failed"`。

`evidenceRefs` 只引用 deciding Turn 中已经存在的有界 Item：

```text
itemId
turnId
kind
status
summary
```

可展示测试、命令、工具、文件变更、Diff 和 Artifact 等已记录活动。它们不
复制大输出，不决定 Goal 状态，也不声称构成形式化证明。CLI 显示 reason 和
deciding Turn；Desktop 展示 reason、相关记录和跳转到该 Turn 的入口。

旧 v1 Goal ledger 继续只读兼容；不得为了 outcome 批量重写用户历史。

#### P6.6 `deepcode loop --resume`（已完成）

命令契约：

```text
deepcode loop "new objective"
deepcode loop --resume SESSION_ID
```

新 Goal 与 Resume 模式互斥。Resume 必须复用原 Session、Goal、history 和
默认 workspace：

| Goal 状态 | `--resume` 行为 |
|---|---|
| `active` 且 idle | 显式 Continue 同一 Goal |
| `active` 且 running | 连接并等待现有 Turn |
| `paused` | Resume 后继续 |
| `blocked` | 将显式 `--resume` 视为用户授权重新尝试 |
| `budget_limited` | 只有提供更大 token budget 后才允许 Resume |
| `complete` | 报告既有结果并返回成功，不新建 Goal |
| 不存在 | 返回明确错误，不创建隐藏 Goal |

额外约束：

- 不创建新 Session ID 或 Goal ID；
- 不复制、重写或删除 canonical history；
- 从任意目录运行时，默认使用 Session 原 workspace；
- 只有显式 `--workspace` 才设置进程级 workspace override；
- provider/model/thinking override 只影响下一 Turn；
- 不把 API Key 写入 Session、日志、测试 fixture 或 Git；
- 继续使用同一个 `DeepCodeApplication`、`TurnService` 和
  `GoalExtension`。

预计修改：

- `cli/loop_cli.py`
- `cli/goal_runner.py`
- `cli/tui/goal_controller.py`
- 对应 CLI、Goal recovery 和 Session identity tests

#### P6.7 验收、真实 Smoke 与提交（已完成）

必须先增加确定性失败测试，再修改生产代码。至少覆盖：

1. `CLOSING` 期间提交的消息准确进入当前或 next Turn 一次；
2. 同一 `messageId` 在所有竞态路径均不重复；
3. Goal continuation 抢先启动时，用户消息 Steer 新 continuation；
4. active-idle 派生为 `readyToContinue`；
5. complete + deciding Turn running 派生为 `finishing`；
6. Continue 不修改 objective、Goal ID 或 Session ID；
7. CLI、Desktop、Headless、Automation metadata 来源正确；
8. 跨端同一 Session 保留逐消息来源；
9. complete/blocked reason 可跨重启恢复；
10. evidence 只引用 deciding Turn 的既有 Item；
11. `deepcode loop --resume` 不创建新 Session；
12. budget-limited Resume 不绕过预算；
13. 旧 Goal ledger 和原 Session 无损可读。

最终门禁：

- Python 全量 tests；
- App Server schema/codegen；
- Desktop Vitest、ESLint、TypeScript、production build；
- Tauri/Rust fmt、Clippy `-D warnings`、tests；
- `pre-commit run --all-files`；
- `git diff --check`；
- clean checkout 与仓库外 CLI 启动；
- 真实 CLI 新建 Goal；
- 真实 Desktop 恢复同一 Session 并 Continue；
- 真实 `deepcode loop --resume`；
- 独立断言 Session ID、Goal ID、workspace、history 和最终工作区；
- 全仓库 secret scan，确认 API Key 未进入 diff；
- 全部通过后再创建一个清晰提交。

P6 最终证据（2026-07-28）：

- Python 全量 `722 passed`；Desktop `74 passed`，ESLint、TypeScript、
  protocol check 与 production build 通过；
- Rust fmt、Clippy `-D warnings` 与 tests（`4 passed`）通过；
- `pre-commit run --all-files` 与 `git diff --check` 通过；
- 将完整 tracked diff 与全部新文件投影到 detached clean worktree 后，
  Python `722 passed`，`npm ci`、Desktop tests/lint/build、sidecar clean
  build 以及 Rust 全门禁再次通过；
- clean wheel 在仓库外完整安装，`deepcode --help`、
  `deepcode loop --help`、App Server runtime probe 与打包 prompt resource
  读取通过；验收同时修复了源码目录曾掩盖的 wheel prompt/package 缺失；
- OpenRouter `moonshotai/kimi-k3`（high thinking）真实完成：
  CLI 新建 Goal、Desktop 恢复 canonical Session 并显式 Continue、Desktop
  approval round trip，以及从另一目录执行 `deepcode loop --resume`；
- 真实 Smoke 独立断言 Session/Goal ID、stored workspace、canonical
  history、最终文件字节、逐消息 `client/source` 与 deciding-Turn evidence；
- 扫描 Git tracked/untracked 文件和完整 diff，未发现 OpenRouter API Key。

#### P6 不变边界

P6 不修改：

- AgentRunner 的普通模型/工具循环；
- 任意目录启动和跨目录 Session 恢复；
- canonical Session JSONL 身份与历史；
- Skills、权限、Sandbox、模型/provider/thinking 选择语义；
- Goal 的模型完成判断；
- 多 Agent 控制逻辑；
- 用户自然语言由主 Agent LLM 理解的边界。

P6 完成后仍只有：

```text
CLI ───────┐
Desktop ───┼──▶ ordinary Turn Runtime
Headless ──┘              ▲
                          │
                    GoalExtension
```

---

## 9. 防止屎山的强制门禁

1. **一个执行 loop。** 不再增加 Goal runner 或第二 Application。
2. **一个事实一个 writer。** Turn、Goal、Session 各有唯一写入边界。
3. **稳定 Goal 身份。** Edit 不制造隐藏 revision 或 generation。
4. **严格原语。** Start、Steer、Queue、Interrupt 互不偷偷 fallback。
5. **Typed data / typed errors。** 不解析错误字符串，不用 magic XML 控制语义。
6. **无关键词路由。** 普通用户输入永远原样交给 LLM。
7. **无任务类型 verifier。** 宿主只检查通用生命周期和并发不变量。
8. **无默认第二 LLM。** 完成判断由当前 Agent 基于真实证据请求。
9. **无长期 dual-write。** 旧格式只读，新格式单写。
10. **无 feature-flag 墓地。** P4 必须删除 legacy production path。
11. **不膨胀巨型服务。** 新 Goal 行为不得继续塞进 1000 行 TurnService。
12. **每阶段有失败测试和真实门禁。** 不能只测 mock happy path。
13. **迁移先保数据。** 不批量重写或删除用户 Session ledger。
14. **净复杂度下降。** P4 必须以删除 Coordinator/Evaluator/Attempt 结束。

如果一个工作项需要再引入多个 Manager、Coordinator、Scheduler 或 Router 才能
解释，立即停止实施并重新划分边界。

---

## 10. 完成定义

只有以下条件同时成立，Loop Engineering 才算完成：

- 普通 CLI/Desktop Session 不使用 Goal 也能执行长任务；
- 同一输入框可在运行中自然语言 Steer；
- Stop 后不用退出 Session 即可继续；
- 路由只看 active/idle 和精确 ID，不分析文字语义；
- Queue 是显式投递时机；
- Goal 是 Thread 的薄生命周期扩展；
- Goal-associated 工作仍由普通 Turn 执行；
- Goal Edit 保持稳定 Goal ID，并实时影响 active Turn；
- normal completion 才触发 Goal continuation；
- interrupt 不改 Goal，也不立即续跑；
- terminal error 不形成自动消耗循环；
- Agent 依据代码和工具证据请求 complete/blocked；
- 没有 GoalAttempt、GoalDecision、GoalCoordinator 或默认 evaluator；
- CLI/Desktop 共享 Runtime、GoalExtension 和 SessionStore；
- 旧 Session 与旧 Goal ledger 无损可读；
- stale Turn/Goal 操作不能影响新状态；
- Stop 后旧 Turn 不再产生未知副作用；
- 全量 deterministic tests 和真实 LLM smoke 通过。

Loop Engineering 的价值不来自更多状态，而来自更少、更正交的状态：唯一
Thread/Turn loop 负责执行，Goal 只提供持久目标和正常 idle 时的延续。
