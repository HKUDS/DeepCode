# Deep Code + Ruflo — 工作环境配置

## 量化交易命令速查
- **完整参考**: `.deepcode/skills/deepcode-vault/data/vault/notes/量化交易系统命令完整参考.md`
- **DOCX 手册**: `量化交易系统_命令参考手册_v4_20260721_182818.docx`
- **入口**: `python quant_trader.py <命令>` (在 `F:/DEEPCODE/`)
- **每日更新**: `python quant_trader.py daily-update` 或 `python quant_trader.py daily`
- **核心命令**: scan | analyze | oversold | first_bearish | mainforce | pit_lift | chanlun | yixian | macro | hot_sector | heatmap | backtest | morning_scan | health

## Deep Code 项目根目录

- **项目根**: `F:/DEEPCODE`
- **Deep Code CLI 源码**: `F:/DEEPCODE/deepcode-cli-source/`
- **Deep Code 工作目录**: `F:/DEEPCODE/core/`
- **settings.json**: `F:/DEEPCODE/settings.json`（包含所有 MCP 服务、环境变量、技能开关）
- **MCP 规范配置**: `F:/DEEPCODE/mcp_servers_canonical.json`（唯一真相源）
  - 修改 MCP 服务请编辑此文件，然后运行 `python scripts/sync_mcp_config.py`
  - 自动同步到 `settings.json` + `.mcp.json`
- **命令插件**: `F:/DEEPCODE/core/commands/`（quant_trader.py 的插件系统）
  - 在 commands/ 下创建 `xxx.py` 定义 `run(args)` 即可注册新命令
  - 自动发现，无需注册，114 个原生命令通过 elif 回退继续可用

## API 提供商

- **HEADROOM 代理端点**: `http://127.0.0.1:8787/v1`（MCP 服务器启动后后台自动拉起，不阻塞 Deep Code）
- **默认模型**: `deepseek-v4-flash`
- **API Key**: `<your-api-key>`
- Ruflo 已配置 OpenAI provider 指向此端点

## Ruflo 命令入口

- 全局命令: `claude-flow`（别名 `ruflo`）
- 版本: v3.25.6
- MCP 配置: `.mcp.json`（包含 Ruflo + 所有 Deep Code MCP 服务）

## Rules

- Do what has been asked; nothing more, nothing less
- NEVER create files unless absolutely necessary — prefer editing existing files
- NEVER create documentation files unless explicitly requested
- NEVER save working files or tests to root — use `/src`, `/tests`, `/docs`, `/config`, `/scripts`
- ALWAYS read a file before editing it
- 所有 WORD 报告必须使用 `quant_trading/report_utils.py` 模板（楷体 + Light Grid Accent 1 表格 + Heading1/Heading2 层级），不得用 office-pro MCP 创建
- **quant_trading/ 目录结构已重组**（2026-07-18）：
  - 从扁平 160 文件 → 7 个领域子包（`strategies/` `analysis/` `data/` `ml/` `infra/` `reports/` `ops/`）
  - 原位置保留了向后兼容的桩模块，所有 `from quant_trading.xxx import yyy` 继续可用
  - 备份在 `quant_trading/_backup_pre_reorg/`
- Qoder 借鉴四大工具：`python quant_trader.py task`（任务看板）、`code_search <关键词>`（代码搜索）、`knowledge query <问题>`（知识引擎）、`agent <任务>`（多 Agent 分工团队）
- Agent Team v2 特性：对抗式审查（Reviewer 强制≥3个问题+2轮）、上下文沙箱（任务级JSON隔离）、断线恢复（--resume 从断点续传）
- MiniMax Code 借鉴：对抗式审查 / 上下文沙箱 / Pi Agent 状态恢复全部已实现
- NEVER commit secrets, credentials, or .env files
- NEVER add a `Co-Authored-By` trailer to user commits unless this project's `.claude/settings.json` has `attribution.commit` set (#2078). The Claude Code Bash tool may suggest one in its default commit-message template — ignore it. `Co-Authored-By` is semantic authorship attribution under git/GitHub convention; the tool is the facilitator, not a co-author.
- Keep files under 500 lines
- Validate input at system boundaries

## Agent Comms (SendMessage-First Coordination)

Named agents coordinate via `SendMessage`, not polling or shared state.

```
Lead (you) ←→ architect ←→ developer ←→ tester ←→ reviewer
              (named agents message each other directly)
```

### Spawning a Coordinated Team

```javascript
// ALL agents in ONE message, each knows WHO to message next
Agent({ prompt: "Research the codebase. SendMessage findings to 'architect'.",
  subagent_type: "researcher", name: "researcher", run_in_background: true })
Agent({ prompt: "Wait for 'researcher'. Design solution. SendMessage to 'coder'.",
  subagent_type: "system-architect", name: "architect", run_in_background: true })
Agent({ prompt: "Wait for 'architect'. Implement it. SendMessage to 'tester'.",
  subagent_type: "coder", name: "coder", run_in_background: true })
Agent({ prompt: "Wait for 'coder'. Write tests. SendMessage results to 'reviewer'.",
  subagent_type: "tester", name: "tester", run_in_background: true })
Agent({ prompt: "Wait for 'tester'. Review code quality and security.",
  subagent_type: "reviewer", name: "reviewer", run_in_background: true })

// Kick off the pipeline
SendMessage({ to: "researcher", summary: "Start", message: "[task context]" })
```

### Patterns

| Pattern | Flow | Use When |
|---------|------|----------|
| **Pipeline** | A → B → C → D | Sequential dependencies (feature dev) |
| **Fan-out** | Lead → A, B, C → Lead | Independent parallel work (research) |
| **Supervisor** | Lead ↔ workers | Ongoing coordination (complex refactor) |

### Rules

- ALWAYS name agents — `name: "role"` makes them addressable
- ALWAYS include comms instructions in prompts — who to message, what to send
- Spawn ALL agents in ONE message with `run_in_background: true`
- After spawning: STOP, tell user what's running, wait for results
- NEVER poll status — agents message back or complete automatically

## Deep Code MCP 服务器清单

`.mcp.json` 中已集成以下 MCP 服务器（Deep Code 全部保留 + Ruflo 自身）：

| 类别 | MCP 服务器 |
|------|-----------|
| **Ruflo 编排** | `claude-flow` — 多智能体编排、记忆、Hook 系统 |
| **A股金融** | `cn-financial`, `china-stock`, `akshare-one`, `aktools`, `ashare` |
| **全球金融** | `tradingview`, `finstack`, `yfinance` |
| **AI 增强** | `deepseek-direct` — DeepSeek 智能路由 |
| **浏览器** | `playwright` — 网页自动化 |
| **文件系统** | `filesystem` — 本地文件读写 |
| **数据库** | `sqlite`, `duckdb`, `postgres` |
| **代码协作** | `github` — GitHub API 集成 |
| **鸿蒙开发** | `deveco-mcp`, `harmonyos`, `harmonyos-best-practices`, `hometrans` |
| **办公文档** | `office-pro` — DOCX 创建编辑 |
| **Windows 自动化** | `winapp` — 桌面应用 UI 自动化 |
| **笔记** | `notion` — Notion API |
| **CAD 设计** | `autocad`, `freecad` |
| **游戏** | `burnrate` — 策略游戏 |
| **其他** | `headroom` — 代理端点管理 |

## Swarm & Routing

### Config
- **Topology**: hierarchical-mesh (anti-drift)
- **Max Agents**: 15
- **Memory**: hybrid
- **HNSW**: Enabled
- **Neural**: Enabled

```bash
npx @claude-flow/cli@latest swarm init --topology hierarchical --max-agents 8 --strategy specialized
```

### Agent Routing

| Task | Agents | Topology |
|------|--------|----------|
| Bug Fix | researcher, coder, tester | hierarchical |
| Feature | architect, coder, tester, reviewer | hierarchical |
| Refactor | architect, coder, reviewer | hierarchical |
| Performance | perf-engineer, coder | hierarchical |
| Security | security-architect, auditor | hierarchical |

### When to Swarm
- **YES**: 3+ files, new features, cross-module refactoring, API changes, security, performance
- **NO**: single file edits, 1-2 line fixes, docs updates, config changes, questions

### 3-Tier Model Routing

| Tier | Handler | Use Cases |
|------|---------|-----------|
| 1 | Agent Booster (WASM) | Simple transforms — skip LLM, use Edit directly |
| 2 | Haiku | Simple tasks, low complexity |
| 3 | Sonnet/Opus | Architecture, security, complex reasoning |

## Memory & Learning

### Before Any Task
```bash
npx @claude-flow/cli@latest memory search --query "[task keywords]" --namespace patterns
npx @claude-flow/cli@latest hooks route --task "[task description]"
```

### After Success
```bash
npx @claude-flow/cli@latest memory store --namespace patterns --key "[name]" --value "[what worked]"
npx @claude-flow/cli@latest hooks post-task --task-id "[id]" --success true --store-results true
```

### MCP Tools (use `ToolSearch("keyword")` to discover)

| Category | Key Tools |
|----------|-----------|
| **Memory** | `memory_store`, `memory_search`, `memory_search_unified` |
| **Bridge** | `memory_import_claude`, `memory_bridge_status` |
| **Swarm** | `swarm_init`, `swarm_status`, `swarm_health` |
| **Agents** | `agent_spawn`, `agent_list`, `agent_status` |
| **Hooks** | `hooks_route`, `hooks_post-task`, `hooks_worker-dispatch` |
| **Security** | `aidefence_scan`, `aidefence_is_safe`, `aidefence_has_pii` |
| **Hive-Mind** | `hive-mind_init`, `hive-mind_consensus`, `hive-mind_spawn` |

### Background Workers

| Worker | When |
|--------|------|
| `audit` | After security changes |
| `optimize` | After performance work |
| `testgaps` | After adding features |
| `map` | Every 5+ file changes |
| `document` | After API changes |

```bash
npx @claude-flow/cli@latest hooks worker dispatch --trigger audit
```

## Agents

**Core**: `coder`, `reviewer`, `tester`, `planner`, `researcher`
**Architecture**: `system-architect`, `backend-dev`, `mobile-dev`
**Security**: `security-architect`, `security-auditor`
**Performance**: `performance-engineer`, `perf-analyzer`
**Coordination**: `hierarchical-coordinator`, `mesh-coordinator`, `adaptive-coordinator`
**GitHub**: `pr-manager`, `code-review-swarm`, `issue-tracker`, `release-manager`

Any string works as a custom agent type.

## Build & Test

- ALWAYS run tests after code changes
- ALWAYS verify build succeeds before committing

```bash
npm run build && npm test
```

## CLI Quick Reference

```bash
npx @claude-flow/cli@latest init --wizard           # Setup
npx @claude-flow/cli@latest swarm init --v3-mode     # Start swarm
npx @claude-flow/cli@latest memory search --query "" # Vector search
npx @claude-flow/cli@latest hooks route --task ""    # Route to agent
npx @claude-flow/cli@latest doctor --fix             # Diagnostics
npx @claude-flow/cli@latest security scan            # Security scan
npx @claude-flow/cli@latest performance benchmark    # Benchmarks
```

26 commands, 140+ subcommands. Use `--help` on any command for details.

## Setup

```bash
claude mcp add claude-flow -- npx -y ruflo@latest mcp start
npx ruflo@latest doctor --fix
```

> The background `daemon` is optional. It runs interval workers that each spawn
> a headless `claude` session, so it consumes tokens continuously. Start it only
> if you want those sweeps: `npx ruflo@latest daemon start` (self-stops after 12h
> by default; `--ttl 0` to disable, `daemon status --all` to audit running daemons).

**Agent tool** handles execution (agents, files, code, git). **MCP tools** handle coordination (swarm, memory, hooks). **CLI** is the same via Bash.

## 效率与路由纪律(2026-08 提速版)

> 用户级 `C:\Users\raymo\.deepcode\CLAUDE.md` 已有完整版,此处为项目级要点。

### 模型路由
- 默认 `deepseek-v4-flash`,日常任务不切 Pro;复杂推理(多步架构/安全/缠论)才升级。
- 有 `router-mcp` 时优先 `router_query` 自动路由;本地数据查询走本地计算,不上云。
- 保持上下文前缀稳定,让 DeepSeek 缓存命中(命中 ¥0.02/M vs 原价 ¥1-3/M)。

### 编程任务免费通道(强制)
- 所有编程/代码任务一律 `router_tier_query(tier='code')`:首选智谱 `glm-4.7-flash`(免费·编程SOTA),降级硅基 `Qwen3-Coder-30B-A3B`(免费) → `Qwen2.5-7B` → NIM `gpt-oss-20b`(均免费),免费全挂才落付费 flash 兜底。
- 禁止纯代码任务直接用主模型生成代码;规划/工具选择用主模型,代码生成交免费通道。
- 复杂推理(架构设计、多步重构、安全)用主模型或 `tier='deep'`,不用 30B 硬扛。

### 代码结构纪律(嵌套防"互踩",强制)
1. 先骨架后填充:复杂嵌套先输出带注释占位的完整骨架,再逐层填充;禁止先建空壳再补内容。
2. 一次性输出完整嵌套结构,不要先出顶层再回头改;修结构用重写整个函数,不打补丁。
3. 嵌套 >3 层拆中间变量/辅助函数,分段生成。
4. 结构优先:先保证括号/缩进/层级闭合,再谈风格。
5. 输出前自检括号配对与层级对齐,发现空壳/重复结构立即整体重写。

### 效率
1. 批量读取,并行工具调用,不重复读同一文件
2. 合并相邻编辑,少建文件,搜索用 grep/glob
3. 窗口 128k,长会话及时精简上下文
4. 不手动补跑已异步化的索引/记忆 hook

### MCP 精简
- 用户级已 park:`sqlite` `duckdb` `github` `office-pro` `sequential-thinking` `deepcode-engine` `deepcode-sandbox` `deepcode-streaming` `deepcode-knowledge` `deepcode-telemetry` `deepcode-starlark` `deepcode-app-server`
- 常驻:`router-mcp` `playwright` `fetch` `deepcode-cerebellum` `deepcode-agent` `deepcode-agent-sdk`
- 需要被 park 的工具时,先 `mcpServersParked` 启用,用完再 park。
