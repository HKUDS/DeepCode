---
name: deepcode-agent
description: >
  DeepCode Agent — 完整 Agent 体系 (v3.0 统一版)。
  线程派生系统 (codex.exe移植) + Agent SDK 外部调用 (Claude Code移植)。
  - 内部: Agent消息类型/线程派生树/动态工具注册/SQLite持久化
  - 外部: HTTP API / MCP stdio / Python嵌入调用
version: 3.0.0
author: DeepCode + RE (codex.exe v0.145.0 + Claude Code v2.1.216)
date: 2026-07-29
tags: [agent, session, tracking, messaging, threading, spawn, sdk, api, integration]
---

# DeepCode Agent

移植自 **codex.exe** 的 Agent Message 类型系统。

## 对标项

| codex.exe | DeepCode Agent |
|:----------|:---------------|
| `AgentMessagePlan` | `AgentPlan` |
| `AgentMessageItem` | `AgentMessage` |
| `AgentMessageContentDeltaEvent` | `AgentMessage(delta=True)` |
| `DynamicToolCall` + `CallToolResult` | `ToolCall` |
| `CommandExecutionItem` + `CommandBeginEvent` | `CommandExecution` |
| `SubagentStart` / `SubagentStop` | `create_sub_agent()` |
| `AgentSession` | `AgentSession` |

## 消息类型 (21 种)

| 类型 | 说明 |
|:----|:-----|
| `reasoning` | 推理过程 |
| `plan` | 执行计划 |
| `tool_call` | 工具调用 |
| `tool_result` | 工具结果 |
| `command_execution` | 命令执行 |
| `command_begin` | 命令开始 |
| `command_end` | 命令结束 |
| `file_change` | 文件变更 |
| `web_search` | 网络搜索 |
| `image_generation` | 图片生成 |
| `mcp_tool_call` | MCP 工具调用 |
| `sub_agent_activity` | 子 Agent 活动 |
| `collab_agent_tool_call` | 协作 Agent |
| `context_compact` | 上下文压缩 |
| `error` | 错误 |
| `text` | 文本 |

## 用法

### CLI

```bash
# 创建计划
python agent.py plan "分析项目结构"

# 记录推理
python agent.py reason "先看package.json, 再看src目录"

# 记录工具调用
python agent.py tool --name Read --input '{"path":"package.json"}' \
  --result '{"content": "..."}'

# 记录命令
python agent.py command "npm test" --stdout "All tests passed"

# 查看状态
python agent.py status

# 导出完整 JSON
python agent.py export
```

### MCP Server

```json
"deepcode-agent": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-agent/agent.py",
    "--mcp"
  ]
}
```

### Python 嵌入

```python
from agent import AgentSession, AgentMessageType

sess = AgentSession("分析项目")
sess.start_turn()

# 记录推理
sess.add_reasoning("检查依赖关系...")

# 记录工具调用
tc = sess.add_tool_call("Read", {"path": "Cargo.toml"})
tc.complete({"content": "[package]\nname = \"codex\""})

# 记录命令
ce = sess.add_command("cargo build")
ce.complete(stdout="Compiling...", exit_code=0)

# 导出
print(sess.export_json())
```

## Agent 线程派生系统 (v2.0.0 新增)

移植自 codex.exe 的 SQLite 持久化 Agent 线程架构，对标 `threads` / `thread_spawn_edges` / `thread_dynamic_tools` / `logs` 四表。

### 核心概念

| 概念 | codex.exe 表 | 说明 |
|:------|:------------|:-----|
| Agent 线程 | `threads` | 完整生命周期 (9种状态) |
| 派生树 | `thread_spawn_edges` | 父→子层级关系 |
| 动态工具 | `thread_dynamic_tools` | 每线程独立注册/卸载 |
| 线程日志 | `logs` | 线程级日志 |

### 9 种 Agent 类型

`coder` `reviewer` `tester` `planner` `researcher` `coordinator` `shell_executor` `file_editor` `general`

### CLI

```bash
python agent_thread_manager.py spawn "analyze binary" --type researcher
python agent_thread_manager.py spawn "implement X" --type coder --parent <id>
python agent_thread_manager.py forest
python agent_thread_manager.py stats
```

### MCP Server

```json
"deepcode-agent-threads": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-agent/agent_thread_manager.py", "--mcp"]
}
```

### Python

```python
from agent_thread_manager import AgentThreadManager, AgentType
mgr = AgentThreadManager()
root = mgr.spawn("analyze", agent_type=AgentType.RESEARCHER)
child = mgr.spawn("implement", agent_type=AgentType.CODER, parent_thread_id=root.id)
tree = mgr.get_full_tree(root.id)
mgr.stop_thread_cascade(root.id)
```

## Agent View 配置 (v2.1.0 — P2-12 Claude Code 对齐)

对标 Claude Code 的 Agent View 配置系统:
`--agent-color`, `--agent`, `--plugins`, `--mcp-config`, `--permission-mode`, `--model`, `--effort`

### CLI 参数

```bash
# 启动 Agent View (可视化多 agent 协作)
python agent_view.py

# 指定 agent 颜色
python agent_view.py --agent-color "#FF6B6B"

# 指定默认 agent 类型
python agent_view.py --agent reviewer

# 加载额外插件目录
python agent_view.py --plugins ./my-plugins

# 指定分派会话的默认 MCP 配置
python agent_view.py --mcp-config ./dispatched-mcp.json

# 默认权限模式 (分派会话)
python agent_view.py --permission-mode plan

# 分派会话的默认模型
python agent_view.py --model deepseek-v4-pro

# 分派会话的默认 effort 级别
python agent_view.py --effort high
```

### 配置结构

```json
{
  "agent_view": {
    "enabled": true,
    "default_agent": "general",
    "agent_color": "#4ECDC4",
    "dispatch_defaults": {
      "permission_mode": "default",
      "model": "deepseek-v4-flash",
      "effort": "medium",
      "bypass_permissions_available": false,
      "strict_mcp_config": false
    },
    "plugins_dir": "",
    "mcp_config_override": {},
    "max_dispatched_sessions": 5
  }
}
```

### Agent 颜色参考

| Agent 类型 | 颜色 | 色值 |
|:----------|:-----|:-----|
| `general` | 青色 | `#4ECDC4` |
| `coder` | 蓝色 | `#45B7D1` |
| `reviewer` | 橙色 | `#FF6B6B` |
| `tester` | 绿色 | `#96CEB4` |
| `planner` | 紫色 | `#DDA0DD` |
| `researcher` | 黄色 | `#FFEAA7` |
| `coordinator` | 白色 | `#DFE6E9` |
| `shell_executor` | 红色 | `#FF7675` |
| `file_editor` | 灰色 | `#B2BEC3` |

## Agent SDK (v3.0 合并 — 原 deepcode-agent-sdk)

移植自 **Claude Code v2.1.216** 的 `agentSdk.ts`。让 DeepCode 作为 Agent 被外部程序调用。

### 三种模式

| 模式 | 说明 | 端口/协议 |
|:---|:-----|:----------|
| **HTTP API** | RESTful API Server | 8088 (HTTP) |
| **MCP stdio** | 作为 MCP Server 运行 | stdin/stdout |
| **Python 嵌入** | `from agent_sdk_server import DeepCodeAgent` | — |

### 内置工具

| 工具名 | 说明 |
|:------|:-----|
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件 |
| `execute_command` | 执行 Shell 命令 |
| `search_files` | 搜索文件 (通配符) |
| `list_directory` | 列出目录内容 |
| `agent_query` | 向 AI 模型发送查询 |
| `agent_status` | 获取 Agent 状态 |

### 启动 HTTP Server

```bash
python agent_sdk_server.py --http --port 8088
```

```bash
# 列出工具
curl http://127.0.0.1:8088/tools

# 执行工具
curl -X POST http://127.0.0.1:8088/execute \
  -H "Content-Type: application/json" \
  -d '{"tool": "list_directory", "params": {"path": "."}}'
```

### MCP 注册

```json
"deepcode-agent-sdk": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-agent/agent_sdk_server.py", "--mcp"],
  "env": { "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}" }
}
```

### Python 嵌入

```python
from agent_sdk_server import DeepCodeAgent
agent = DeepCodeAgent(workspace="/path/to/project")
agent.register_tool("my_tool", my_handler, "My tool description")
result = await agent.execute("read_file", {"path": "test.txt"})
```

### SDK 配置

| 变量 | 默认值 | 说明 |
|:----|:------|:----|
| `DEEPSEEK_API_KEY` | — | API 密钥 (agent_query) |
| `DEEPCODE_AGENT_PORT` | 8088 | HTTP 端口 |
| `DEEPCODE_AGENT_HOST` | 127.0.0.1 | HTTP 绑定地址 |
