# DeepCode Auto-Research 方法

状态：研究提案
范围：新版通用 DeepCode Agent Harness
非目标：Paper2Code、论文转代码或旧论文复现工作流

## 1. 研究命题

DeepCode Auto-Research 研究的不是如何让 Agent 无限循环，也不是如何自动搜索
论文。它研究以下问题：

> 一个开放、模型无关的 Coding Agent，能否从连续的软件工程任务中学习，自动
> 演化自己的 Skills、Memory、Hooks、工具策略与协作拓扑，并在未来任务、其他
> 仓库和其他模型上变得更可靠、更便宜？

暂定研究名称：

> **DeepCode-Evolve: Cross-Model Lifelong Harness Evolution for Coding Agents**

DeepCode 在这个方向上的核心定位是 Agent Harness，而不是某个特定模型的外壳。
Codex 和 Claude Code 可以解决软件任务；DeepCode-Evolve 应当进一步研究怎样让
解决这些任务的 Harness 从真实结果中持续进化。

## 2. Auto-Research 的最小闭环

一次有效的 Auto-Research 循环必须包含：

```text
定义目标、预算和冻结评估器
  -> 使用当前 HarnessRevision 执行任务
  -> 收集轨迹、代码差异、测试、成本与错误
  -> 根据证据提出一个可证伪的 Harness 修改假设
  -> 生成一个或多个候选 HarnessRevision
  -> 在干净 Session 和隔离 Worktree 中运行对照实验
  -> 由 Agent 无法修改的外部 Evaluator 评分
  -> 在 held-out 任务上验证
  -> 晋升、保留到 Pareto 档案，或者回滚
```

只有“提出假设、执行实验、外部评分、保留或回滚”全部存在时，才能称为
Auto-Research。普通 Goal continuation 仍然只是长期 Agent 执行。

## 3. 与 RSI 的关系

当研究对象是外部代码或算法时，它是普通 Auto-Research。当研究对象变成
DeepCode 自身的 Harness 时，它成为受控的 Recursive Self-Improvement：

```text
L0  单次回答反思
L1  演化 Prompt、Skill、Memory 和 Hook
L2  演化工具、Agent 拓扑和运行策略
L3  演化 DeepCode 核心代码或模型权重
```

第一阶段只实现 L1，并逐步开放 L2。没有稳定的外部评估器、回归集与安全门禁
以前，不开放 L3。

“某个 Benchmark 得分提高”不等于 RSI。系统还必须证明：

- 改进可以迁移到未见任务；
- 改进不会破坏以前已经会做的任务；
- 改进能够迁移到其他仓库或模型；
- 改进没有通过修改评估器、污染 Memory 或扩大权限作弊；
- 改进后的系统能够更有效地提出下一代改进。

## 4. 为什么适合新版 DeepCode

现有新版运行时已经提供 Auto-Research 所需的大部分底层原语：

- 多 Provider 和模型切换；
- 每个 Turn 的不可变 `ExecutionProfile`；
- Skill 的稳定 ID、SHA-256 revision 和 Turn 快照；
- 持久化 Session、Turn、Usage、事件与 Goal；
- PreTool、PostTool、Stop、Compaction 等 Hook 接口；
- 子 Agent、隔离 Worktree 和冲突检测；
- Automation、预算、权限、Sandbox 与崩溃恢复；
- SWE-bench 中已有的预测与外部评分分离。

Auto-Research 不应修改现有 Thread/Turn/Goal 语义，也不应重新引入通用 Goal
Evaluator。实验调度应当是普通 Turn 之上的独立应用层。

## 5. 可演化边界

第一阶段允许演化：

| 层次 | 可变内容 |
| --- | --- |
| Skill | `SKILL.md`、Skill 脚本、引用资料、工具约束 |
| Context | System/role prompt、任务模板、上下文组织策略 |
| Memory | Memory schema、经验提取、检索、压缩与失效策略 |
| Hooks | 工具前后反馈、完成检查、错误诊断与防回归规则 |
| Tools | 工具描述、选择策略和结果压缩策略 |
| Team | Agent 角色、依赖拓扑、并行度和每个角色使用的模型 |
| Runtime | 重试、Compaction、预算分配和停止策略 |

始终冻结：

- 外部 Evaluator、Hidden tests 和任务划分；
- 权限、审批、安全策略与 Sandbox；
- 事件、Usage 和实验审计记录；
- 数据污染检查；
- Harness 晋升与回滚协议；
- 最终 held-out 评测环境。

Agent 不能同时拥有候选生成权和最终评分权。

## 6. 旗舰挑战：RepoEvolve

RepoEvolve 给 Agent 一组来自同一仓库、按时间排列的软件工程任务。Agent 在完成
每个任务后可以更新 Harness，但未来任务和最终测试保持隐藏。

建议每个仓库配置 20 个任务：

```text
Task 01-08  adaptation：允许利用测试反馈学习
Task 09-12  validation：选择和回滚 HarnessRevision
Task 13-16  future：评测同仓库前向迁移
Task 17-18  cross-repo：评测跨仓库迁移
Task 19-20  cross-model：更换底层模型评测迁移
```

任务应覆盖：

- Bug 修复和回归测试；
- 跨文件功能开发；
- 调试、性能与资源问题；
- 依赖升级和迁移；
- 安全修复；
- 长期重构；
- 文档、实现和测试不一致；
- 会使旧 Memory 失效的非平稳仓库变化。

## 7. 核心指标

### 7.1 能力指标

- **Resolved rate**：任务最终解决率；
- **Forward transfer**：学习早期任务后，未来任务相对静态 Harness 的提升；
- **Learning-curve AUC**：成功率随任务序列增长的面积；
- **Retention**：新 revision 对过去任务的回归率；
- **Cross-repo transfer**：在未见仓库上的迁移收益；
- **Cross-model transfer**：同一 Harness 在不同模型上的迁移收益。

### 7.2 效率指标

- 每个 resolved task 的 Token、费用和墙钟时间；
- 工具调用数、无效重复调用数和重试数；
- 上下文长度、Memory 检索量和缓存命中；
- 并行 Agent 数量与实际加速比。

### 7.3 演化质量指标

- **Promotion precision**：晋升 revision 在 held-out 上真实提升的比例；
- **Skill utility**：有无该 Skill 的对照收益，而不是调用频率；
- **Generalization gap**：adaptation 与 held-out 得分差；
- **Harness complexity**：提升是否依赖不断膨胀的 Prompt、Skill 或 Agent 数量；
- **Metaproductivity**：该 revision 产生优秀下一代 revision 的能力。

### 7.4 安全指标

- 权限绕过和危险命令尝试；
- 修改评估器、测试或数据划分的尝试；
- Hidden 信息进入 Memory 的污染率；
- Skill、Tool 或 Hook 演化引入的安全回归；
- 伪造执行结果或无证据完成声明。

## 8. 实验与对照协议

每个候选实验必须固定并记录：

- DeepCode commit；
- HarnessRevision；
- Provider、模型、Thinking Level 和生成参数；
- Skill revision 列表；
- Memory snapshot；
- Hook 和 Tool policy revision；
- Agent topology；
- 任务和数据版本；
- 容器或环境摘要；
- Token、时间、费用与随机种子；
- Workspace patch、完整事件轨迹和外部评分。

至少设置以下 Baseline：

1. 静态 DeepCode，不学习；
2. 只使用 Persistent Memory；
3. 只演化 Skills；
4. Skills + Memory；
5. 完整 Harness evolution；
6. Codex 原生推荐配置；
7. Claude Code 原生推荐配置。

公平比较分为两条赛道：

- **产品赛道**：每个产品使用自己的推荐模型，但费用、时间和并行预算相同；
- **架构赛道**：在 DeepCode 内固定同一个底层模型，对各 Harness 组件做消融。

LLM Judge 可以用于诊断和生成候选，但不能作为唯一最终评分器。能使用测试、
构建、性能指标或确定性检查时，必须优先使用机械 Evaluator。

## 9. Lab 应用层

建议新增独立领域模型：

```text
LabCampaign
  |-- TaskSuite
  |-- HarnessRevision
  |    |-- ExecutionProfile
  |    |-- SkillRevision[]
  |    |-- MemorySnapshot
  |    |-- HookRevision
  |    |-- ToolPolicy
  |    `-- AgentTopology
  |-- Trial
  |-- Evaluation
  |-- LineageEdge
  `-- PromotionDecision
```

职责边界：

- `Turn` 是一次真实 Agent 执行；
- `Goal` 是跨 Turn 持续追求的用户目标；
- `Automation` 是重复触发稳定工作；
- `LabCampaign` 是产生候选、组织对照、调用外部评分和选择 revision 的实验；
- `HarnessRevision` 是可重放的 Agent 配置快照；
- `Evaluation` 是由冻结评估器产生的事实。

Lab 不实现第二个 AgentRunner。它通过现有 `ExecutionCoordinator` 创建普通 Turn，
并在 Turn 外部管理实验关系。

## 10. 候选生成与选择

Evolver 每次只能提交明确的单一或少量可解释修改，并附带：

- 问题诊断；
- 修改假设；
- 预期影响；
- 可能回归；
- 所需对照实验；
- 回滚条件。

不要只维护一个 best-so-far。维护 Pareto Archive：

```text
质量更高
成本更低
速度更快
结构更简单
跨模型迁移更好
安全风险更低
```

相互独立的优秀候选应继续保留，以避免单一路径早熟收敛。晋升必须经过
validation 和安全回归；最终报告只在冻结的 future set 上运行一次。

## 11. Memory 与 Skill 的特殊规则

### Memory

- 每个 HarnessRevision 使用独立快照；
- 区分项目事实、任务经历和可迁移策略；
- 每条策略保留来源 Trial、适用范围和失效条件；
- 不自动把成功轨迹全部写入长期 Memory；
- 定期通过对照实验删除无用或有害记忆；
- 禁止保存 Hidden tests、答案或评估器内部状态。

### Skill

- Skill revision 必须可重放；
- 创建、修改、拒绝和回滚都保留决策历史；
- 调用率不能作为效用指标；
- 通过无 Skill、旧 Skill 和新 Skill 三组对照判断收益；
- 同一个 Skill 分别记录各模型和任务族上的效用；
- 新 Skill 不能提升权限，也不能扩大冻结的可写范围。

## 12. 多 Agent 与模型路由

研究模式需要声明式 `AgentTopology`，例如：

```yaml
roles:
  planner:
    model: claude
  implementer:
    model: codex
  tester:
    model: qwen-coder
  reviewer:
    model: gemini
edges:
  - planner -> implementer
  - implementer -> tester
  - tester -> reviewer
```

拓扑优化必须与最强单 Agent、同模型多 Agent和随机拓扑进行对照。增加 Agent 数量
只有在成功率或墙钟时间的收益超过额外成本时才算改进。

## 13. 第一阶段 MVP

MVP 只实现 Skill evolution，暂不修改核心 Agent 代码：

1. 增加 `LabCampaign`、`HarnessRevision`、`Trial` 和 `Evaluation`；
2. 将现有 SWE-bench predict/score 分离封装为冻结 Evaluator；
3. 为每个 Trial 创建干净 Session 和隔离 Worktree；
4. 记录 Skill revision、模型配置、Usage、Patch、测试和事件轨迹；
5. Evolver 根据成功与失败轨迹生成候选 Skill revision；
6. 在 adaptation 任务上试验，在 validation 任务上晋升；
7. 提供 revision 比较、回滚和 lineage 展示；
8. 发布 DeepCode static、memory-only、skill-only 和 skill-evolution 消融结果。

MVP 的成功条件不是单个任务得分上涨，而是：

- future 任务成功率相对静态 DeepCode 有稳定提升；
- 平均成本没有不可接受地膨胀；
- 过去任务回归受控；
- 至少一部分 Skill 能迁移到第二种模型；
- 所有提升均可由第三方重新运行。

## 14. 后续阶段

### Phase 2：Context and Memory Evolution

加入结构化经验、失效策略、对照检索和 ACE/ReasoningBank 风格的增量 Playbook。

### Phase 3：Cross-Model Harness Compiler

输入仓库、任务分布、模型集合和成本预算，输出 Skills、Hooks、Memory、模型路由
和 AgentTopology 的 Pareto 配置。

### Phase 4：Safe Harness RSI

在强隔离、Hidden regression 和人工晋升下，允许修改工具代码及有限的 Agent
Runtime。安全策略、Evaluator、Sandbox 和实验账本始终不可演化。

## 15. 最终定位

研究表达：

> Can an open, model-agnostic coding agent learn from a stream of software
> engineering tasks and autonomously evolve a safer, cheaper, and more
> transferable agent harness?

产品表达：

> DeepCode is the coding agent that learns how to code better over time,
> across repositories and across models.
