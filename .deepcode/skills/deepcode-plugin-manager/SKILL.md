---
name: deepcode-plugin-manager
description: >
  Plugin Manager — MCP 包装的插件热加载系统。
  支持从 URL/ZIP/本地目录三种来源安装插件，内建 SHA256 签名校验。
  工具: plugin__install, plugin__list, plugin__remove, plugin__info.
  Use when the user asks to install a plugin, add a skill, load a plugin,
  or mentions plugin URL loading, hot-loading skills, or plugin management.
version: 1.0.0
author: DeepCode
date: 2026-07-30
tags: [plugin, skill, loader, mcp, integration]
---

# DeepCode Plugin Manager

将 `plugin_loader.py` 包装为 MCP 服务，AI 可直接通过工具调用管理插件。

## 工具一览

| 工具 | 说明 |
|:-----|:------|
| `plugin__install` | 安装插件 — 支持 URL/ZIP/目录三种来源 + SHA256 校验 |
| `plugin__list` | 列出所有已安装插件（含未注册） |
| `plugin__remove` | 卸载插件（删除文件 + 清理注册表） |
| `plugin__info` | 查看插件详情（注册信息 + 文件结构） |

## 插件结构要求

```
plugin.zip / plugin-dir/
├── SKILL.md          ← 必需 (YAML frontmatter + 提示词)
├── plugin.json       ← 可选 (name/version/author/entry/dependencies)
├── checksum.sha256   ← 可选 (SHA256 签名)
└── *.py / *.js       ← 可选 (工具脚本)
```

## 安全机制

1. **SHA256 校验**: URL 安装支持 `--checksum` 参数显式验证
2. **内嵌 checksum**: ZIP 内含 `checksum.sha256` 文件自动验证
3. **自动查找**: 尝试从 URL 同路径加载 `.sha256` 文件
4. **结构验证**: 缺少 `SKILL.md` 的插件拒绝安装

## 安装路径

所有插件安装到 `.deepcode/skills/{name}/`，注册信息写入 `.deepcode/plugin_registry.json`。

## 使用示例

```
# AI 调用示例：
plugin__install source="https://example.com/my-plugin.zip"
plugin__list
plugin__info name="my-plugin"
plugin__remove name="my-plugin"
```
