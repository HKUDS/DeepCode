"""DeepCode core — 四层架构

层间依赖: CLI → Agent → Provider → Tool (禁止跨层调用)

┌─────────────────────────────────────────────────┐
│ CLI Layer  (cli/)                                │
│  用户交互 / 命令路由 / TUI / Loop Orchestration  │
├─────────────────────────────────────────────────┤
│ Agent Layer  (agent_runtime/ + compat/)          │
│  智能体生命周期 / MCP 工具管理 / Hook 系统      │
├─────────────────────────────────────────────────┤
│ Provider Layer  (providers/)                     │
│  LLM 模型适配 (OpenAI/Anthropic/DeepSeek 等)    │
├─────────────────────────────────────────────────┤
│ Tool Layer  (harness/tools/)                     │
│  Shell / 文件 / 搜索 / 权限 / 沙箱              │
├─────────────────────────────────────────────────┤
│ Infrastructure  (config / sessions / events /    │
│                  observability / loop / team)    │
└─────────────────────────────────────────────────┘

Public surface:
  core.providers       — LLM SDK 适配器
  core.agent_runtime   — 智能体循环 + MCP 客户端
  core.config          — 配置加载 + ${VAR} 解析
  core.harness         — 工具沙箱 + 权限
  core.events          — 事件总线
  core.loop            — 自动循环 (Dream/Backpressure)
  core.team            — 多智能体协作
"""
