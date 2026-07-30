---
name: deepcode-improvements
description: >
  7 Claude Code-inspired improvements for DEEPCODE: plan permission mode,
  plugin URL loading, MCP strict mode, telemetry skeleton, multi-provider
  abstraction, local settings level, extended hook events.
  Use when the user asks about DEEPCODE features, improvements, or
  mentions permission modes, plugin loading, MCP config, telemetry,
  provider switching, settings hierarchy, or hook events.
version: 1.0.0
date: 2026-07-27
tags: [deepcode, improvements, architecture]
---

# DEEPCODE Improvements

7 项改进，基于 CLAUDE CODE 2.1.216 逆向分析。

## 1. Plan 权限模式

```json
"permissions": {
  "defaultMode": "plan",
  "modes": {
    "plan": { "description": "Plan mode: analyze and propose, never execute" }
  }
}
```

plan 模式下 Agent 只分析和规划，不执行任何工具调用。适用于代码审查、
架构设计等"只看不动"的任务。

**用法**: `--permission-mode plan` 或在 settings 中设置 `defaultMode: "plan"`。

## 2. Plugin URL 加载

```json
"pluginUrls": [
  "https://example.com/my-plugin.zip"
]
```

从 URL 动态加载插件 .zip 文件（无需提前下载到本地）。

**用法**: `--plugin-url https://example.com/plugin.zip`

## 3. MCP Strict Mode

```json
"strictMcpConfig": true
```

启用后，只使用 `--mcp-config` 指定的 MCP server，忽略 settings.json
中的所有其他 MCP 配置。用于隔离调试。

**用法**: `--strict-mcp-config`

## 4. Telemetry 骨架

```json
"telemetry": {
  "enabled": false,
  "exporter": "otlp",
  "otlpEndpoint": "http://localhost:4317",
  "otlpHeaders": {},
  "otlpInsecure": false,
  "prometheusHost": "127.0.0.1",
  "prometheusPort": 9464
}
```

OpenTelemetry 兼容的遥测出口。默认关闭。

**Env vars**: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_EXPORTER_OTLP_INSECURE`, `OTEL_EXPORTER_PROMETHEUS_HOST`,
`OTEL_EXPORTER_PROMETHEUS_PORT`

## 5. 多 Provider 抽象

```json
"provider": {
  "active": "deepseek",
  "providers": {
    "deepseek": { "type": "openai", "apiBase": "https://api.deepseek.com" },
    "anthropic": { "type": "anthropic", "apiBase": "https://api.anthropic.com" },
    "bedrock": { "type": "bedrock", "region": "${AWS_REGION}" },
    "vertex": { "type": "vertex", "project": "${GCP_PROJECT}" }
  }
}
```

统一 provider 抽象层，支持 OpenAI-compatible / Anthropic / Bedrock / Vertex。

**切换**: `--provider anthropic` 或在 settings 中设置 `provider.active: "anthropic"`。

## 6. Local 级 Settings

三层级继承: `user → project → local`

| 层级 | 文件 | 用途 |
|------|------|------|
| user | `~/.deepcode/settings.json` | 全局默认 |
| project | `.deepcode/settings.json` | 项目共享 |
| local | `.deepcode/settings.local.json` | 本机专属（不提交 git） |

local 设置覆盖 project 设置，project 覆盖 user。

**配置文件已存在**: `.deepcode/settings.local.json`

## 7. 扩展 Hook 事件

```json
"hooks": {
  "PreToolUse": [], "PostToolUse": [],
  "SessionStart": [], "SessionEnd": [],
  "Route": [], "OnError": [],
  "PreTask": [], "PostTask": []
}
```

新增事件: `Route`（路由决策）、`OnError`（错误处理）、
`PreTask`（任务开始前）、`PostTask`（任务结束后）、`SessionEnd`（会话结束）。

## 启用建议

这 7 项改进已全部配置到 `.deepcode/settings.json` 和 `.deepcode/settings.local.json`。
使用 `deepcode-engine` MCP 的 `permission_config` / `mcp_status` 等工具可查看状态。
