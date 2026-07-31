---
name: deepcode-hooks
description: >
  DeepCode Hook System — 移植自 Claude Code 的 Hook 架构.
  支持 Pre/Post 工具钩子、会话钩子、错误钩子、路由钩子。
  对标 Claude Code: hook-handler.cjs, pre-bash, post-edit,
  session-restore, session-end, pre-task, post-task, route.
version: 1.0.0
author: DeepCode + Ghidra RE (Claude Code v2.1.216)
date: 2026-07-26
tags: [hooks, pipeline, automation, security]
---

# DeepCode Hook System

移植自 **Claude Code v2.1.216** 的完整 Hook 架构 — 通过 Ghidra + v8asm 从 V8 字节码中逆向提取。

## 逆向成果

通过 `v8asm` 反汇编 + `bun` 段字符串提取，还原了 Claude Code 完整的 Hook 系统设计：

| 来源 | 工具 | 发现 |
|:----|:----|:----|
| `.bun` V8 字节码 | `v8asm disassembler` | 13 种 Hook 事件 |
| V8 heap snapshot | `extract_bun_hooks.py` | 4 种 Hook 类型 (command/prompt/agent/mcp_tool) |
| PE 字符串提取 | Ghidra + 二进制搜索 | Hook 输入/输出 JSON Schema |

## Claude Code Hook 事件 (完整列表)

| # | 事件 | 时机 | 用途 |
|:-|:----|:----|:-----|
| 1 | **PreToolUse** | 工具执行前 | 可阻塞工具调用—对标 `beforeCommand`/`beforeWrite` |
| 2 | **PostToolUse** | 工具执行成功 | 记录结果—对标 `afterCommand`/`afterWrite` |
| 3 | **PostToolUseFailure** | 工具执行失败 | 错误处理—对标 `onError` |
| 4 | **PermissionRequest** | 权限检查 | 权限决策注入 |
| 5 | **Notification** | 通知事件 | 通知类型过滤 |
| 6 | **Stop** | 中止请求 | 安全停止—对标 `onCancel` |
| 7 | **UserPromptSubmit** | 用户提交 | 提示注入检测 |
| 8 | **SessionStart** | 会话开始 | 状态恢复—对标 `sessionStart` |
| 9 | **Setup** | 插件安装 | 插件初始化 |
| 10 | **UserPromptExpansion** | 提示展开 | 用户输入预处理 |
| 11 | **SubagentStop** | 子 Agent 停止 | Subagent 生命周期 |
| 12 | **WorktreeCreate** | Git Worktree 创建 | Git 隔离工作区 |
| 13 | **WorktreeRemove** | Git Worktree 删除 | 清理工作区 |
| 14 | **PreCompact** | 压缩/紧凑前 | 状态持久化保护 |

## Hook 类型

| 类型 | 说明 | JSON type 值 |
|:----|:----|:------------|
| **Command Hook** | 执行 Shell 命令: `{ "type": "command", "command": "prettier --write $FILE" }` | `command` |
| **Prompt Hook** | LLM 评估条件: `{ "type": "prompt", "prompt": "Is this safe?" }` | `prompt` |
| **Agent Hook** | 运行 Agent 工具链: `{ "type": "agent", "tools": ["read", "write"] }` | `agent` |
| **MCP Tool Hook** | 调用 MCP 工具: `{ "type": "mcp_tool", "mcp_tool": "tool_name" }` | `mcp_tool` |

## Hook 协议 (stdin/stdout JSON)

### Hook 输入 (stdin JSON)

```json
{
  "session_id": "abc123",
  "tool_name": "Bash",
  "tool_input": { "command": "ls -la" },
  "tool_response": { "exit_code": 0 }    // PostToolUse 才有
}
```

### Hook 输出 (stdout JSON)

```json
{
  "systemMessage": "显示给用户的消息",
  "continue": true,
  "decision": "block",         // PreToolUse: 阻塞工具
  "reason": "安全策略阻止",
  "hookSpecificOutput": {
    "additionalContext": "额外上下文",
    "permissionDecision": "allowed"
  }
}
```

## 对标项

| Claude Code | DeepCode Hooks |
|:-----------|:---------------|
| `PreToolUse` | `beforeCommand` / `beforeWrite` / `beforeEdit` |
| `PostToolUse` | `afterCommand` / `afterWrite` / `afterEdit` |
| `PostToolUseFailure` | `onError` |
| `PermissionRequest` | (权限门控集成) |
| `SessionStart` | `sessionStart` |
| `Setup` | `startup` |
| `Stop` | (停止事件) |
| `UserPromptSubmit` | (提示注入) |
| `pre-bash` / `post-edit` / `session-restore` | 通用事件系统覆盖 |
| `hook-handler.cjs` | `hooks.py` |
| hook 异常检测 | `exit_code` + `error_message` |

## 支持的事件

| 事件 | 对应 Claude Code | 触发时机 |
|:----|:----------------|:--------|
| **beforeCommand** | PreToolUse (Bash) | 命令执行前 |
| **afterCommand** | PostToolUse (Bash) | 命令执行后 |
| **beforeWrite** | PreToolUse (Write) | 文件写入前 |
| **afterWrite** | PostToolUse (Write) | 文件写入后 |
| **beforeEdit** | PreToolUse (Edit) | 文件编辑前 |
| **afterEdit** | PostToolUse (Edit) | 文件编辑后 |
| **beforeRead** | PreToolUse (Read) | 文件读取前 |
| **onError** | PostToolUseFailure | 发生错误 |
| **preTask** | (任务级) | 任务开始 |
| **postTask** | (任务级) | 任务结束 |
| **sessionStart** | SessionStart | 会话开始 |
| **sessionEnd** | SessionEnd | 会话结束 |
| **route** | (路由决策) | 路由决策 |
| **startup** | Setup | 系统启动 |
| **shutdown** | — | 系统关闭 |

## 用法

### CLI

```bash
# 注册 — 文件写入前自动 lint
python hooks.py register \
  --name pre-lint \
  --event beforeWrite \
  --handler "npx eslint {filePath}" \
  --type shell \
  --priority 10

# 注册 — 命令执行前安全校验
python hooks.py register \
  --name safety-check \
  --event beforeCommand \
  --handler "python safety_check.py" \
  --type python \
  --priority 100

# 列出
python hooks.py list

# 触发
python hooks.py trigger --event beforeWrite \
  --ctx '{"filePath":"/path/to/file.py","toolName":"Write"}'
```

### settings.json 集成

兼容现有配置格式，新增 Hook 配置节：

```json
{
  "hooks": {
    "beforeWrite": "echo [hook] 即将修改: {filePath}",
    "afterEdit": "git add {filePath}",
    "beforeCommand": "python safety_check.py",
    "onError": "python notify_error.py",
    "sessionStart": "python restore_state.py",
    "sessionEnd": "python persist_state.py"
  }
}
```

模板变量: `{filePath}`, `{command}`, `{exitCode}`, `{error}`, `{timestamp}`

### MCP Server

```json
"deepcode-hooks": {
  "command": "python",
  "args": [
    "F:/DEEPCODE/.deepcode/skills/deepcode-hooks/hooks.py",
    "--mcp"
  ]
}
```

### Python 嵌入

```python
from hooks import HookManager, Hook, HookContext, HookEvent

mgr = HookManager()

# 注册
mgr.register(Hook(
    name="auto-commit",
    event=HookEvent.AFTER_EDIT,
    handler="git add {filePath} && git commit -m 'auto: {filePath}'",
    type="shell",
    priority=50,
))

# 触发
ctx = HookContext(
    event=HookEvent.AFTER_EDIT,
    tool_name="Edit",
    file_path="/path/to/file.py",
    exit_code=0,
)
results = await mgr.trigger(HookEvent.AFTER_EDIT, ctx)
for r in results:
    print(f"{r['hook']}: {r['status']} ({r['duration']}s)")
```

## Hook 类型

| 类型 | 执行方式 | 适用场景 |
|:----|:--------|:--------|
| `shell` | `subprocess` | 简单命令、git 操作 |
| `python` | `subprocess` | 复杂逻辑校验 |
| `node` | `subprocess` | npm 生态工具 |

## 安全

- 每个 Hook 有独立超时保护（默认 30s）
- 异常不会级联 — 一个 Hook 失败不影响其他 Hook
- Hook 通过环境变量接收上下文（`DEEPCODE_*` 系列变量）
