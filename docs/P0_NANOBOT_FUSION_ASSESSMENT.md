# P0.8 · nanobot 进程内融合评估

**结论:P0 不动 nanobot 代码。** 三栈合一后,内核已经是单一循环;nanobot 的融合属于 P3/P5 的增量,且有明确路径。

## 现状

- `nanobot/` 是仓库内嵌的完整独立 agent 框架(自有 loop、内建 filesystem/shell/web/spawn 工具、SKILL.md 技能、cron、多渠道),自带 `pyproject.toml`,也是独立发布的产品。
- 关键事实:**`core/agent_runtime/runner.py` 本身就是 nanobot runner 的移植**(见其模块 docstring)。所以"循环层"的融合在 P0 已经完成——内核就是那个循环;nanobot 目录里的副本只服务于独立的 nanobot 产品。
- 当前唯一的集成方式是 HTTP 外挂:`nanobot/nanobot/agent/tools/deepcode.py` 通过 `DEEPCODE_API_URL` 把 DeepCode 当外部工具调用。

## 融合路径(按主计划阶段)

| 阶段 | 动作 | 依据 |
|------|------|------|
| P0(本次) | 无代码移动;确认内核=nanobot loop 血统,消除了"第三套循环"的概念负担 | 已完成 |
| P3 循环工程 | 把 nanobot 的 `CronTool` / cron 调度移植到内核会话上(loop engineering 层的排程能力) | nanobot 已有 croniter 方案,拿来即用 |
| P5 生态 | 渠道网关按 ChannelBridge 模式进程内消费内核事件流(渠道永不直连引擎);SKILL.md 技能加载器作为 harness 扩展面 | OpenHarness/Ohmo 已验证该形态 |
| P5 之后 | nanobot 内嵌副本达到功能对齐后退役(与独立发包节奏协调) | 避免双源漂移 |

## 风险

- nanobot 上游继续演进 → 与 core 移植版产生漂移。缓解:每次同步上游时 diff `nanobot/agent/runner.py` 与 `core/agent_runtime/runner.py`,机制改进双向回灌(本次 P0 给内核加的 `should_stop_callback` / `max_injection_cycles` 属于超集,不冲突)。
