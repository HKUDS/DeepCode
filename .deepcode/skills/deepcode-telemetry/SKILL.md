---
name: deepcode-telemetry
description: >
  DeepCode Telemetry — 移植自 CODEX.EXE 的 OpenTelemetry 遥测栈。
  Trace Spans, Metrics, OTLP/Prometheus/JSON 导出。
  对标 codex.exe: opentelemetry-otlp-0.31.0, SessionTelemetry, model_preferences.
version: 1.0.0
author: DeepCode + RE (codex.exe v0.145.0)
date: 2026-07-28
tags: [telemetry, otel, metrics]
---

# DeepCode Telemetry

移植自 **CODEX.EXE** 的完整遥测栈。

## 模块

| 模块 | 文件 | 对标 CODEX | 状态 |
|:------|:-----|:----------|:-----|
| Trace/Metrics | `telemetry.py` | opentelemetry-otlp-0.31.0 | ✅ 可用 |
| Provider 路由 | `provider_router.py` | aws-smithy-runtime | ❌ **已废弃** → 合并到 router-mcp |

## 遥测 (telemetry.py)

- **Trace Spans**: 工具调用 / LLM 调用 / 会话生命周期
- **Metrics**: 耗时 / Token 用量 / 错误率 / 计数器
- **导出**: JSON File / OTLP HTTP / Prometheus / Console
- **环境变量**: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_METRIC_EXPORT_INTERVAL

## Provider 路由 (⚠️ 已废弃)

`provider_router.py` 的功能已被 **router-mcp** MCP Server 取代。

替代方案:
- settings.json `provider.active` — Provider 选择
- `router_query(force_model="deepseek-v4-pro")` — 指定模型

## CLI

```bash
python telemetry.py demo
```

## MCP Server

```json
"deepcode-telemetry": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-telemetry/telemetry.py", "--mcp"]
}
```
