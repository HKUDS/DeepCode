# P1 · 内核与协议(L0/L1 增量)

安全底座(`docs/P1_SECURITY_BASE.md`)之外,P1 的另一半:L0 规范化流 + L1 事件/消息模型,以及消除 provider 层反模式。**全程机制、非决策;每个模块单一职责、有真实消费者、附测试——不留投机性死代码。**

## 交付

| 组件 | 文件 | 作用 |
|------|------|------|
| ModelCompat 声明式 resolver | `core/providers/model_compat.py` | 收敛 openai_compat 里散落的 `model_id.includes()` 判断(温度/推理模型/thinking/token 字段/effort 拼写)为一个纯函数;`_build_kwargs` 从 ~100 行 tangle 降为薄装配 |
| LLMEvent 规范化流(L0) | `core/events/llm_events.py` | provider 流的唯一事件词汇(text/reasoning/tool_call_*/usage/error)+ `LLMResponse→事件` converter + 序列化 |
| 结构化 parts(L1) | `core/events/parts.py` | 消息 parts 模型(text/reasoning/tool 状态机 pending→running→completed\|error)+ kernel dict→parts converter;供 UI/持久化/回放 |
| SQ/EQ 协议(L1) | `core/events/protocol.py` | `Submission{id,op}`/`Event{id,msg}` 判别联合(UserInput/Interrupt/Shutdown ↔ TurnStarted/ToolStarted/ToolCompleted/AgentMessage/TaskComplete) |
| AgentSession(L1) | `core/events/session.py` | **真实消费者**:消费 Op、经内核 hook 实时发 Event、驱动 AgentRunner;任何前端(TUI/headless/web/测试)接同一协议,不直连内核 |
| 配置权限接入 | `core/config.py` `SecurityConfig` + `core/harness/policy.py` | `deepcode_config.json` 的 `security` 块(mode/rules/sandbox)经 `rules_from_config` 接入;env `DEEPCODE_PERMISSION_MODE` 覆盖配置覆盖默认 |

## 关键设计决策(杜绝屎山)

1. **ModelCompat 是重构而非新增**——它把已存在的散落判断收敛到单一事实源,让 provider 代码**更少**屎山味(军规 8、反模式 #15)。以等价测试 + 真实 Poe gpt-5.4 验证行为逐字不变。
2. **三层词汇干净分离**:LLMEvent(provider 流)/ parts(消息模型)/ SQ-EQ(UI 协议)各司其职,不混为一谈(比计划里把 step/usage 混在一个 union 更清晰)。
3. **只建有真实生产者的类型**:parts 只有 text/reasoning/tool——`step`/`patch` 留到 P2 有 git 快照生产者时再加,不建空壳。
4. **AgentSession 是真实集成**:经内核既有 hook 缝实时发事件,证明词汇承重,而非事后投影的死代码。
5. **每个模块 ≤ ~200 行、纯数据 + 纯函数、无 IO**。

## 验证

- **单测 +40**(97 total):ModelCompat 11、LLMEvent 6、parts 7、AgentSession 7、policy 8 + 既有安全层。
- **真实 Poe gpt-5.4 等价**:重构后 `_build_kwargs` 对 gpt-5.4 产出的 wire kwargs 逐项不变(无 temperature、max_completion_tokens、reasoning_effort=low、裸模型名),真实调用通过。
- **全链路回归**:paper9 端到端无回退。

## 与 P2 的衔接

SQ/EQ + parts + LLMEvent 是 P2 多前端(TUI / headless JSON / Web 改接事件流)的地基:各前端订阅同一 `Event` 流即可,无需触碰内核。
