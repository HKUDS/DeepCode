#!/usr/bin/env python3
"""
MCP 配置健康检查脚本 — 防止路径断裂和能力声明错误。

检查项:
  1. 入口文件是否存在 (command + args 中的所有文件路径)
  2. Python MCP 服务器的 capabilities.tools 是否声明 listChanged
  3. 必需的 environment variables 是否可解析

用法:
    python scripts/validate_mcp.py              # 检查所有
    python scripts/validate_mcp.py --json       # JSON 输出 (适合 CI)
    python scripts/validate_mcp.py --quiet      # 仅输出错误
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / ".deepcode" / "settings.json"

# — 这些 command 是包管理器/运行时，不需要检查路径 —
_RUNTIME_COMMANDS = {"npx", "node", "python", "python3", "uv", "uvx", "npm", "yarn", "pnpm"}

# — capabilities 声明正则：匹配有问题的模式 —
_BAD_CAPABILITIES_RE = re.compile(
    r'"capabilities"\s*:\s*\{\s*"tools"\s*:\s*\{\s*\}\s*\}'
)

# — 文件路径判定：args 中以 .py / .js / .mjs / .ts 结尾的视为需要检查 —
_FILE_EXTENSIONS = {".py", ".js", ".mjs", ".ts", ".cjs"}


def is_file_path(arg: str) -> bool:
    """判断一个 arg 是否为需要检查的本地文件路径。"""
    if not arg:
        return False
    # 排除明显的 npm 包名 / 命令行 flag
    if arg.startswith("-") or arg.startswith("@"):
        return False
    # 排除纯命令名 (不含路径分隔符)
    if "/" not in arg and "\\" not in arg:
        return False
    # 检查是否以已知文件扩展名结尾
    return any(arg.endswith(ext) for ext in _FILE_EXTENSIONS)


def check_python_capabilities(file_path: Path) -> list[str]:
    """检查 Python MCP 服务器是否在 capabilities 中声明了 listChanged。"""
    issues = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues  # 文件读取失败由路径检查负责

    if _BAD_CAPABILITIES_RE.search(content):
        issues.append(
            f"  [WARN] capabilities 声明不完整: \"tools\": {{}} 应改为 \"tools\": {{\"listChanged\": true}}"
        )
    return issues


def validate() -> dict:
    """返回 {ok: bool, issues: [str], stats: {total, ok, failed, skipped}}"""
    issues = []
    stats = {"total": 0, "ok": 0, "failed": 0, "skipped": 0}

    if not SETTINGS_PATH.exists():
        issues.append(f"[FATAL] 配置文件不存在: {SETTINGS_PATH}")
        return {"ok": False, "issues": issues, "stats": stats}

    try:
        config = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        issues.append(f"[FATAL] settings.json 解析失败: {e}")
        return {"ok": False, "issues": issues, "stats": stats}

    mcp_servers = config.get("mcpServers", {})
    if not mcp_servers:
        issues.append("[WARN] 没有配置任何 MCP 服务器")
        return {"ok": True, "issues": issues, "stats": stats}

    stats["total"] = len(mcp_servers)

    for name, server in mcp_servers.items():
        # 跳过注释行
        if name.startswith("//"):
            stats["skipped"] += 1
            continue

        server_ok = True
        command = server.get("command", "")
        args = server.get("args", [])

        # —— 检查 1: command 是否可执行 ——
        if command in _RUNTIME_COMMANDS:
            exe_path = shutil.which(command)
            if exe_path is None:
                issues.append(f"[{name}] command '{command}' 未安装或不在 PATH 中")
                server_ok = False
        elif command:
            # 非标准运行时：先尝试 PATH 解析（如 mcp-server-fetch 这类
            # 全局安装的命令），解析不到再当作文件路径检查。
            if shutil.which(command) is None:
                cmd_path = Path(command)
                if not cmd_path.is_absolute():
                    # 相对路径相对于项目根
                    cmd_path = PROJECT_ROOT / command
                if not cmd_path.exists():
                    issues.append(f"[{name}] command 路径不存在: {cmd_path}")
                    server_ok = False

        # —— 检查 2: args 中的文件路径 ——
        for arg in args:
            if is_file_path(arg):
                arg_path = Path(arg)
                if not arg_path.is_absolute():
                    arg_path = PROJECT_ROOT / arg
                if not arg_path.exists():
                    issues.append(f"[{name}] 入口文件不存在: {arg_path}")
                    server_ok = False
                    continue
                # —— 检查 3: Python 文件的 capabilities 声明 ——
                if arg_path.suffix == ".py":
                    cap_issues = check_python_capabilities(arg_path)
                    for ci in cap_issues:
                        issues.append(f"[{name}] {arg_path.name}:{ci}")
                        server_ok = False

        # —— 检查 4: env 变量引用 ——
        env = server.get("env", {})
        for key, value in env.items():
            if isinstance(value, str) and "${" in value:
                refs = re.findall(r'\$\{([^}]+)\}', value)
                for ref in refs:
                    if ref not in os.environ and ref != value:
                        issues.append(
                            f"[{name}] 环境变量 ${ref} 未设置 (env.{key})"
                        )
                        # 不标记为 fatal — 可能是 CI 设置

        if server_ok:
            stats["ok"] += 1
        else:
            stats["failed"] += 1

    return {
        "ok": stats["failed"] == 0,
        "issues": issues,
        "stats": stats,
    }


def main():
    json_output = "--json" in sys.argv
    quiet = "--quiet" in sys.argv

    result = validate()

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["ok"] else 1)

    s = result["stats"]
    print(f"\n{'='*60}")
    print(f"  MCP 健康检查: {s['total']} 台服务器")
    print(f"  [OK] 正常: {s['ok']}  |  [FAIL] 异常: {s['failed']}  |  [SKIP] 跳过: {s['skipped']}")
    print(f"{'='*60}")

    if result["issues"]:
        for issue in result["issues"]:
            print(issue)
        print()

    if result["ok"]:
        print("[ALL GOOD] 所有 MCP 服务器配置健康!\n")
    else:
        print(f"[FIX] 发现 {s['failed']} 个问题，请修复后重试。\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
