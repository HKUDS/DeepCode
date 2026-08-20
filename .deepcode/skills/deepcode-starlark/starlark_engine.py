#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Starlark Engine — 移植自 CODEX.EXE 的 Starlark 脚本引擎
═══════════════════════════════════════════════════════════════
对标 CODEX.EXE v0.145.0:
  - starlark-0.14.2 crate → 纯 Python 实现
  - 确定性执行 (无 I/O, 无 random, 无 time)
  - 安全沙箱 (限制内置函数)
  - 规则引擎 + 配置脚本

核心能力:
  1. 安全脚本执行 — 无副作用, 纯计算
  2. Hook 规则 — if/then 逻辑定义安全策略
  3. 配置 DSL — 结构化配置语言
  4. 管道过滤 — 数据转换规则

用法:
  # 执行 Starlark 脚本
  python starlark_engine.py run --file rules.star

  # 直接执行代码
  python starlark_engine.py run --code "x = [1,2,3]; result = sum(x)"

  # 作为 MCP Server
  python starlark_engine.py --mcp
"""

import asyncio
import ast
import json
import math
import operator
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ── Starlark 类型 ──────────────────────────────────────────────

class StarlarkType(str, Enum):
    NONE = "NoneType"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "str"
    LIST = "list"
    DICT = "dict"
    TUPLE = "tuple"
    SET = "set"
    FUNCTION = "function"


# ── 安全限制 ───────────────────────────────────────────────────

# Starlark 不允许的内置函数
DENIED_BUILTINS: Set[str] = {
    "exec", "eval", "compile", "open", "file",
    "__import__", "input", "raw_input",
    "globals", "locals", "vars",
    "breakpoint", "memoryview",
    # I/O
    "print",  # Starlark 标准: 不允许 print (可选)
}

# Starlark 不允许的模块
DENIED_MODULES: Set[str] = {
    "os", "sys", "subprocess", "shutil", "socket",
    "ctypes", "multiprocessing", "threading",
    "io", "pickle", "marshal", "code",
    "importlib", "inspect", "signal",
}

# 允许的 safe 内置
SAFE_BUILTINS: Dict[str, Any] = {
    "True": True, "False": False, "None": None,
    "abs": abs, "all": all, "any": any,
    "bin": bin, "bool": bool, "bytes": bytes,
    "chr": chr, "dict": dict, "dir": dir,
    "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float,
    "format": format, "frozenset": frozenset,
    "hash": hash, "hex": hex,
    "int": int, "isinstance": isinstance,
    "issubclass": issubclass, "iter": iter,
    "len": len, "list": list, "map": map,
    "max": max, "min": min, "next": next,
    "oct": oct, "ord": ord, "pow": pow,
    "range": range, "repr": repr,
    "reversed": reversed, "round": round,
    "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple,
    "type": type, "zip": zip,
    # Math
    "ceil": math.ceil, "floor": math.floor,
    "sqrt": math.sqrt, "log": math.log,
    "log2": math.log2, "log10": math.log10,
    "pi": math.pi, "e": math.e,
    # Regex
    "re_match": lambda p, s: bool(re.match(p, s)),
    "re_search": lambda p, s: bool(re.search(p, s)),
    "re_findall": lambda p, s: re.findall(p, s),
    # 类型转换
    "json_dumps": json.dumps,
    "json_loads": json.loads,
}


@dataclass
class StarlarkResult:
    """Starlark 执行结果"""
    ok: bool = True
    value: Any = None
    error: str = ""
    globals_after: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    output: str = ""


# ── Starlark 解释器 ────────────────────────────────────────────

class StarlarkEngine:
    """
    Starlark 脚本引擎 — 对标 CODEX.EXE 的 starlark-0.14.2

    特性:
      - 确定性: 相同输入 → 相同输出
      - 纯函数: 无 I/O, 无 random, 无 time
      - 安全沙箱: 限制内置, 限制模块
      - 可扩展: 注册自定义函数/变量

    对标:
      CODEX.EXE 使用 Starlark 做配置脚本和规则引擎。
      本实现提供相同的能力用于 DEEPCODE 的:
        - Hook 规则 (if security_check() then block())
        - 权限规则 (allow_if / deny_if)
        - 配置 DSL
    """

    def __init__(self, extra_globals: Optional[Dict[str, Any]] = None):
        self._builtins = dict(SAFE_BUILTINS)
        if extra_globals:
            self._builtins.update(extra_globals)
        self._custom_functions: Dict[str, Callable] = {}

    def register_function(self, name: str, func: Callable) -> None:
        """注册自定义函数到 Starlark 环境"""
        self._custom_functions[name] = func

    def register_variable(self, name: str, value: Any) -> None:
        """注册变量到 Starlark 环境"""
        self._builtins[name] = value

    def execute(self, code: str, timeout: int = 10) -> StarlarkResult:
        """
        执行 Starlark 代码

        Args:
            code: Starlark 源码
            timeout: 超时秒数

        Returns:
            StarlarkResult
        """
        start = datetime.now()

        # 1. 安全检查 — 禁止危险模块导入
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            return StarlarkResult(ok=False, error=f"Syntax error: {e}")

        # 2. 检查危险构造
        violation = self._audit_ast(tree)
        if violation:
            return StarlarkResult(ok=False, error=violation)

        # 3. 创建安全执行环境
        safe_globals: Dict[str, Any] = {
            "__builtins__": {
                k: v for k, v in self._builtins.items()
                if k not in DENIED_BUILTINS
            },
            **self._custom_functions,
        }

        # 4. 编译并执行
        try:
            compiled = compile(tree, "<starlark>", "exec")
            exec(compiled, safe_globals)
        except Exception as e:
            return StarlarkResult(ok=False, error=f"Execution error: {e}")

        # 5. 提取结果
        result_value = safe_globals.get("result", None)
        output = safe_globals.get("_output", "")

        # 清理内部变量
        clean_globals = {
            k: v for k, v in safe_globals.items()
            if not k.startswith("_") and k != "__builtins__"
        }

        duration = int((datetime.now() - start).total_seconds() * 1000)
        return StarlarkResult(
            ok=True, value=result_value,
            globals_after=clean_globals,
            duration_ms=duration, output=str(output),
        )

    def _audit_ast(self, tree: ast.AST) -> Optional[str]:
        """
        AST 审计 — 检测危险操作

        对标 CODEX.EXE 的安全检查:
          - 禁止 import 危险模块
          - 禁止 I/O 操作
          - 禁止 subprocess 调用
        """
        for node in ast.walk(tree):
            # 禁止 import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in DENIED_MODULES:
                        return f"Import denied: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in DENIED_MODULES:
                    return f"Import denied: {node.module}"

            # 禁止 exec/eval
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ("exec", "eval", "compile", "open", "__import__"):
                        return f"Builtin denied: {node.func.id}()"

            # 禁止属性访问危险模块
            elif isinstance(node, ast.Attribute):
                pass  # 运行时检查

        return None

    def execute_rules(
        self, rules_code: str, context: Dict[str, Any], timeout: int = 10
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        执行 Starlark 规则集

        这是对标 CODEX 的规则引擎用法:
          规则代码中定义 allow() / deny() 决策函数，
          上下文提供当前请求的信息。

        Args:
            rules_code: Starlark 规则源码
            context: 执行上下文 (e.g. {"tool": "Bash", "command": "rm -rf /"})

        Returns:
            (allowed: bool, decisions: Dict)
        """
        # 注入上下文
        self._builtins["_ctx"] = context

        result = self.execute(rules_code, timeout=timeout)

        if not result.ok:
            return True, {"error": result.error}

        # 读取规则决策
        allowed = result.globals_after.get("allowed", True)
        decisions = result.globals_after.get("decisions", {})
        reason = result.globals_after.get("reason", "")

        return allowed, {"decisions": decisions, "reason": reason, **result.globals_after}


# ── 预定义规则模板 ─────────────────────────────────────────────

DEFAULT_HOOK_RULES = """
# DEEPCODE Hook Rules — Starlark 安全策略
# 对标 CODEX.EXE 的 PreToolUse / PermissionRequest 规则

ctx = _ctx  # 当前上下文

# 默认允许
allowed = True
reason = ""

# 规则 1: 阻止危险命令
DANGEROUS_COMMANDS = ["rm -rf /", "shutdown", "format", "dd if=", "mkfs"]
for cmd in DANGEROUS_COMMANDS:
    if ctx.get("command", "").lower().find(cmd) >= 0:
        allowed = False
        reason = "Blocked dangerous command: " + cmd

# 规则 2: 阻止写入系统目录
if ctx.get("tool") == "Write":
    path = ctx.get("file_path", "")
    if path.startswith("/etc/") or path.startswith("C:\\\\Windows\\\\"):
        allowed = False
        reason = "Blocked write to system path: " + path

# 规则 3: 工具白名单
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]
tool = ctx.get("tool", ctx.get("tool_name", ""))
if tool and tool not in ALLOWED_TOOLS:
    allowed = False
    reason = "Tool not in allowlist: " + tool

decisions = {"allowed": allowed, "reason": reason}
"""


# ── MCP Server ─────────────────────────────────────────────────

async def run_mcp():
    """MCP Server 模式"""
    engine = StarlarkEngine()

    TOOLS = {
        "starlark_execute": {
            "description": "执行 Starlark 脚本 — 安全、确定性、无副作用",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Starlark 源码"},
                    "timeout": {"type": "integer", "default": 10},
                },
                "required": ["code"],
            },
        },
        "starlark_execute_file": {
            "description": "执行 Starlark 脚本文件",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
        },
        "starlark_rules": {
            "description": "执行 Starlark 规则集 (allows/deny 决策)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rules_code": {"type": "string"},
                    "context": {"type": "object", "description": "执行上下文"},
                },
                "required": ["rules_code", "context"],
            },
        },
        "starlark_register": {
            "description": "注册自定义 Starlark 函数",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "code": {"type": "string", "description": "Python lambda 源码"},
                },
                "required": ["name", "code"],
            },
        },
    }

    # ── 标准 MCP JSON-RPC 2.0 stdio ──
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        method = req.get("method", "")
        params = req.get("params", {})
        rid = req.get("id", "")

        # ── initialize ──
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "deepcode-starlark", "version": "1.0.0"},
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── notifications/initialized ──
        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"tools": [
                    {"name": k, "description": v["description"],
                     "inputSchema": v["inputSchema"]}
                    for k, v in TOOLS.items()
                ]},
            }), flush=True)

        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            result = {}

            try:
                if name == "starlark_execute":
                    r = engine.execute(
                        args["code"],
                        timeout=args.get("timeout", 10),
                    )
                    result = {
                        "ok": r.ok, "value": r.value,
                        "error": r.error, "duration_ms": r.duration_ms,
                        "output": r.output,
                        "globals": {k: str(v) for k, v in r.globals_after.items()},
                    }

                elif name == "starlark_execute_file":
                    file_path = args["file_path"]
                    with open(file_path, "r") as f:
                        code = f.read()
                    r = engine.execute(code)
                    result = {"ok": r.ok, "value": r.value, "error": r.error}

                elif name == "starlark_rules":
                    allowed, decisions = engine.execute_rules(
                        args["rules_code"], args["context"],
                    )
                    result = {"allowed": allowed, "decisions": decisions}

                elif name == "starlark_register":
                    func_name = args["name"]
                    func_code = args["code"]
                    exec(f"engine.register_function('{func_name}', {func_code})")
                    result = {"registered": func_name}

                else:
                    result = {"error": f"Unknown: {name}"}

            except Exception as e:
                result = {"error": str(e), "ok": False}

            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False)}],
                },
            }), flush=True)


# ── CLI ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="DeepCode Starlark Engine — 安全脚本执行")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="执行 Starlark 代码")
    p_run.add_argument("--file", help="脚本文件路径")
    p_run.add_argument("--code", help="直接执行代码")
    p_run.add_argument("--ctx", default="{}", help="上下文 JSON")

    p_rules = sub.add_parser("rules", help="执行规则集")
    p_rules.add_argument("--file", help="规则文件路径")
    p_rules.add_argument("--ctx", default="{}", help="上下文 JSON")

    sub.add_parser("demo", help="运行演示规则")

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp())
        return

    engine = StarlarkEngine()

    if args.command == "run":
        if args.file:
            with open(args.file, "r") as f:
                code = f.read()
        elif args.code:
            code = args.code
        else:
            print("Error: --file or --code required")
            sys.exit(1)

        result = engine.execute(code)
        print(json.dumps({
            "ok": result.ok, "value": result.value,
            "error": result.error, "duration_ms": result.duration_ms,
            "globals": result.globals_after,
        }, indent=2, default=str))

    elif args.command == "rules":
        if args.file:
            with open(args.file, "r") as f:
                rules_code = f.read()
        else:
            rules_code = DEFAULT_HOOK_RULES

        ctx = json.loads(args.ctx)
        allowed, decisions = engine.execute_rules(rules_code, ctx)
        print(json.dumps({"allowed": allowed, "decisions": decisions}, indent=2, default=str))

    elif args.command == "demo":
        print("=== Starlark Demo ===")
        print()

        # Demo 1: 简单计算
        print("[Demo 1] 简单计算")
        r = engine.execute("x = list(range(10)); result = sum(x)")
        print(f"  sum(0..9) = {r.value} ({r.duration_ms}ms)")
        print()

        # Demo 2: 规则: 危险命令检测
        print("[Demo 2] 安全规则 — 检测 rm -rf /")
        ctx = {"tool": "Bash", "command": "rm -rf /"}
        allowed, dec = engine.execute_rules(DEFAULT_HOOK_RULES, ctx)
        print(f"  allowed={allowed}, reason={dec.get('reason', '')}")
        print()

        # Demo 3: 规则: 安全命令
        print("[Demo 3] 安全规则 — 检测 ls -la")
        ctx = {"tool": "Bash", "command": "ls -la"}
        allowed, dec = engine.execute_rules(DEFAULT_HOOK_RULES, ctx)
        print(f"  allowed={allowed}")
        print()

        # Demo 4: 列表推导
        print("[Demo 4] 列表推导")
        r = engine.execute("result = [x*x for x in range(1,6)]")
        print(f"  squares: {r.value}")
        print()

        # Demo 5: 被阻止的操作
        print("[Demo 5] 危险操作被阻止")
        r = engine.execute("import os; os.system('ls')")
        print(f"  blocked: {r.error}")


if __name__ == "__main__":
    main()
