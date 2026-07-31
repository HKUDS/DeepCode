---
name: deepcode-knowledge
description: >
  DeepCode 统一知识引擎 — Obsidian式知识库 + KV记忆系统二合一。
  知识库: 结构化Markdown笔记、模板(stock/daily/strategy/note)、[[wikilink]]双向链接、
  知识图谱、每日复盘。记忆: KV持久化、多后端自动路由、全文搜索。
  Use when saving analysis results, searching knowledge base, managing memories,
  generating daily reviews, or building personal knowledge systems.
version: 1.0.0
author: DeepCode
date: 2026-07-29
tags: [knowledge, vault, memory, obsidian, notes, templates, graph]
---

# DeepCode Knowledge Engine

合并 `deepcode-vault` (知识库) + `deepcode-memory` (记忆) 的统一知识引擎。

## 架构

```
AI 调用
  │
  ▼
knowledge_server.py (MCP, 14 tools)
  ├── Vault 引擎 (8 tools)
  │   ├── Markdown 笔记 CRUD
  │   ├── [[wikilink]] 双向链接
  │   ├── 知识图谱
  │   ├── 模板渲染 (stock/daily/strategy/note)
  │   └── Obsidian 双向同步
  └── Memory 引擎 (6 tools)
      ├── KV 存储 (save/load/forget)
      ├── 全文搜索
      └── 5 后端自动路由 (ruflo/claude/flow/tokensave/plan)
```

## 14 工具一览

### Vault (知识库)
| 工具 | 说明 |
|:------|:-----|
| `knowledge__vault_status` | 知识库统计 — 笔记数、类型分布、磁盘占用 |
| `knowledge__save_analysis` | 保存分析结果 — 自动模板 + 标签 + [[wikilink]] |
| `knowledge__search_notes` | 全文搜索笔记 |
| `knowledge__read_note` | 读取笔记完整内容 (frontmatter + body) |
| `knowledge__show_graph` | 知识图谱 — 笔记关联网络 |
| `knowledge__daily_log` | 每日复盘 — generate/show/list |
| `knowledge__list_templates` | 列出 4 种分析模板 |
| `knowledge__sync_to_obsidian` | 同步到 Obsidian 仓库 |

### Memory (记忆)
| 工具 | 说明 |
|:------|:-----|
| `knowledge__memory_save` | 保存 KV 记忆 |
| `knowledge__memory_load` | 加载记忆值 |
| `knowledge__memory_search` | 全文搜索记忆 |
| `knowledge__memory_list` | 列出所有键名 |
| `knowledge__memory_forget` | 删除记忆 |
| `knowledge__memory_stats` | 记忆系统统计 |

## MCP 注册

```json
"deepcode-knowledge": {
  "command": "python",
  "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-knowledge/knowledge_server.py"]
}
```

## 使用示例

```
# 保存茅台分析
mcp__deepcode-knowledge__knowledge__save_analysis
  type=stock, symbol=600519, title="茅台技术面分析"

# 搜索历史分析
mcp__deepcode-knowledge__knowledge__search_notes query=茅台

# 查看知识图谱
mcp__deepcode-knowledge__knowledge__show_graph symbol=600519

# 保存会话记忆
mcp__deepcode-knowledge__knowledge__memory_save key=last_task value="分析茅台"

# 加载记忆
mcp__deepcode-knowledge__knowledge__memory_load key=last_task
```
