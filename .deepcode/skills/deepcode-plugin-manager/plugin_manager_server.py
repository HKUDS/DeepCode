#!/usr/bin/env python3
"""
Plugin Manager MCP Server
══════════════════════════
将 plugin_loader.py 包装为 MCP 工具，AI 可通过工具调用直接安装/管理插件。

工具:
  plugin__install   — 安装插件（URL/ZIP/目录 + SHA256 校验）
  plugin__list      — 列出所有插件
  plugin__remove    — 卸载插件

注册到 .mcp.json:
  "deepcode-plugin-manager": {
    "command": "python",
    "args": ["F:/DEEPCODE/.deepcode/skills/deepcode-plugin-manager/plugin_manager_server.py"]
  }
"""

import json
import sys
import os
import io
import contextlib
from pathlib import Path

# 确保能找到 plugin_loader.py
PLUGIN_LOADER_DIR = Path(__file__).resolve().parent.parent.parent  # .deepcode/
sys.path.insert(0, str(PLUGIN_LOADER_DIR))

from plugin_loader import (
    install_from_dir,
    install_from_zip,
    install_from_url,
    list_plugins,
    remove_plugin,
    load_registry,
)


def _silent_call(func, *args, **kwargs):
    """调用 plugin_loader 函数，将其 stdout 重定向到 stderr 以避免污染 MCP JSON"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = func(*args, **kwargs)
    # 将日志输出到 stderr
    log = buf.getvalue()
    if log.strip():
        for line in log.strip().split("\n"):
            print(f"[plugin] {line}", file=sys.stderr, flush=True)
    return result


def _result(ok: bool, data=None, error: str = "") -> str:
    """统一返回格式"""
    resp = {"ok": ok}
    if data is not None:
        resp["data"] = data
    if error:
        resp["error"] = error
    return json.dumps(resp, ensure_ascii=False, indent=2)


# ── MCP 工具处理器 ──────────────────────────────────────────────

MCP_TOOLS = {
    "plugin__install": {
        "description": "安装插件 — 支持 URL/ZIP/本地目录三种来源，可选 SHA256 校验",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "插件来源：URL (https://...)、ZIP 文件路径、或本地目录路径",
                },
                "name": {
                    "type": "string",
                    "description": "插件名称（可选，默认从 plugin.json 或路径名自动识别）",
                },
                "checksum": {
                    "type": "string",
                    "description": "SHA256 签名（可选，仅 URL 安装时使用）",
                },
            },
            "required": ["source"],
        },
        "handler": "handle_install",
    },
    "plugin__list": {
        "description": "列出所有已安装的插件（含已注册和未注册的）",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "handler": "handle_list",
    },
    "plugin__remove": {
        "description": "卸载指定插件（删除文件 + 清理注册表）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要卸载的插件名称",
                },
            },
            "required": ["name"],
        },
        "handler": "handle_remove",
    },
    "plugin__info": {
        "description": "查看插件详情（注册信息 + 目录文件列表）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "插件名称",
                },
            },
            "required": ["name"],
        },
        "handler": "handle_info",
    },
}


def handle_install(params: dict) -> str:
    source = params.get("source", "").strip()
    name = params.get("name")
    checksum = params.get("checksum")

    if not source:
        return _result(False, error="source is required")

    try:
        if source.startswith("http://") or source.startswith("https://"):
            ok = _silent_call(install_from_url, source, plugin_name=name, checksum=checksum)
        elif source.endswith(".zip"):
            ok = _silent_call(install_from_zip, Path(source))
        else:
            ok = _silent_call(install_from_dir, Path(source), plugin_name=name)

        if ok:
            return _result(True, data={"message": f"Plugin installed successfully", "source": source})
        return _result(False, error="Plugin installation failed (see stderr for details)")
    except Exception as e:
        return _result(False, error=str(e))


def handle_list(params: dict) -> str:
    try:
        plugins = _silent_call(list_plugins)
        reg = _silent_call(load_registry)
        registered = list(reg.get("plugins", {}).keys())
        return _result(True, data={
            "total": len(plugins),
            "registered": registered,
            "plugins": plugins,
        })
    except Exception as e:
        return _result(False, error=str(e))


def handle_remove(params: dict) -> str:
    name = params.get("name", "").strip()
    if not name:
        return _result(False, error="name is required")

    try:
        ok = _silent_call(remove_plugin, name)
        return _result(True, data={"message": f"Plugin '{name}' removed"})
    except Exception as e:
        return _result(False, error=str(e))


def handle_info(params: dict) -> str:
    name = params.get("name", "").strip()
    if not name:
        return _result(False, error="name is required")

    try:
        reg = _silent_call(load_registry)
        plugin_info = reg.get("plugins", {}).get(name)
        if not plugin_info:
            return _result(False, error=f"Plugin '{name}' not found in registry")

        # 列出目录结构
        plugin_dir = PLUGIN_LOADER_DIR / "skills" / name
        files = []
        if plugin_dir.exists():
            for f in sorted(plugin_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(plugin_dir)
                    files.append(str(rel))

        return _result(True, data={
            "name": name,
            "registry": plugin_info,
            "directory": str(plugin_dir) if plugin_dir.exists() else None,
            "files": files,
        })
    except Exception as e:
        return _result(False, error=str(e))


# ── MCP stdio 协议 ─────────────────────────────────────────────

HANDLERS = {
    "handle_install": handle_install,
    "handle_list": handle_list,
    "handle_remove": handle_remove,
    "handle_info": handle_info,
}


def handle_mcp_request(request: dict) -> dict:
    """处理 MCP JSON-RPC 请求"""
    req_id = request.get("id")
    method = request.get("method", "")

    # Initialize / tools/list
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "deepcode-plugin-manager",
                    "version": "1.0.0",
                },
            },
        }

    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": name,
                        "description": meta["description"],
                        "inputSchema": meta["inputSchema"],
                    }
                    for name, meta in MCP_TOOLS.items()
                ]
            },
        }

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})

        for name, meta in MCP_TOOLS.items():
            if name == tool_name:
                handler = HANDLERS.get(meta["handler"])
                if handler:
                    result_text = handler(arguments)
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": result_text}
                            ]
                        },
                    }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """MCP stdio 模式：通过 stdin/stdout 通信"""
    # 设置 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_mcp_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }), flush=True)
        except Exception as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"Internal error: {e}"},
            }), flush=True)


if __name__ == "__main__":
    main()
