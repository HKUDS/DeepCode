---
name: deepcode-starlark
description: >
  DeepCode Starlark Engine — 移植自 CODEX.EXE 的 starlark-0.14.2。
  安全脚本执行、确定性计算、Hook 规则引擎、配置 DSL。
  对标 codex.exe: 内置 Starlark 解释器做安全规则和配置。
version: 1.0.0
author: DeepCode + RE (codex.exe v0.145.0)
date: 2026-07-28
tags: [starlark, scripting, rules, security, dsl]
---

# DeepCode Starlark Engine

移植自 **CODEX.EXE** 的 Starlark 脚本引擎 (starlark-0.14.2)。

## 核心能力

| 能力 | 说明 |
|:------|:-----|
| 安全执行 | 无 I/O, 无 random, 无 time — 确定性 |
| 规则引擎 | Hook 规则 / 权限策略 |
| 配置 DSL | 结构化配置语言 |
| 零依赖 | 纯 Python AST 实现 |

## CLI

```bash
python starlark_engine.py run --code "result = sum(range(100))"
python starlark_engine.py rules --ctx '{"tool":"Bash","command":"ls"}'
python starlark_engine.py demo
```

## MCP Server

```json
"deepcode-starlark": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-starlark/starlark_engine.py", "--mcp"]
}
```
