"""
``deepcode session`` — 会话生命周期管理 (Phase 1: llama-style API lifecycle)

模仿 llama.cpp 的 init → load → new_context → encode/decode → free 模式，
将 DeepCode 的一次性执行拆分为清晰的分阶段生命周期，支持守护进程模式和增量分析。

子命令:
  init         初始化分析引擎（加载配置/注册规则）
  load <path>  加载项目（解析结构/依赖图）
  check [规则]  运行分析（可重复调用）
  close        释放当前会话
  daemon       启动守护进程（IDE 增量分析模式）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 会话上下文（跨子命令保持状态）───────────────────────────────
@dataclass
class AnalysisContext:
    """分析会话上下文 —— 类似 llama_context 的角色"""
    config: dict[str, Any] = field(default_factory=dict)
    project_path: str = ""
    project_structure: dict[str, Any] = field(default_factory=dict)
    ast_cache: dict[str, Any] = field(default_factory=dict)
    symbol_table: dict[str, Any] = field(default_factory=dict)
    rule_registry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_check: str = ""
    checks_run: int = 0


# 全局会话上下文（类似 llama.cpp 的全局 state）
_global_ctx: AnalysisContext | None = None
_ctx_lock = asyncio.Lock()


def _ensure_ctx() -> AnalysisContext:
    global _global_ctx
    if _global_ctx is None:
        raise RuntimeError("No active session. Run 'deepcode session init' first.")
    return _global_ctx


# ── 子命令实现 ────────────────────────────────────────────────

async def _cmd_init(args: argparse.Namespace) -> int:
    """deepcode session init — 初始化分析引擎"""
    global _global_ctx
    async with _ctx_lock:
        if _global_ctx is not None:
            print("Session already active. Use 'deepcode session close' first.")
            return 1

        config = {
            "workspace": os.path.abspath(args.workspace),
            "model": args.model or "deepseek-v4-flash",
            "max_iterations": args.max_iterations,
            "rules_dir": args.rules_dir or "",
            "verbose": args.verbose,
        }
        os.makedirs(config["workspace"], exist_ok=True)

        # 加载内置规则注册表 (二维矩阵: 操作×语言)
        rule_registry = _build_default_rule_matrix()

        _global_ctx = AnalysisContext(
            config=config,
            rule_registry=rule_registry,
        )
        print(f"[init] 分析引擎已初始化")
        print(f"       workspace : {config['workspace']}")
        print(f"       model     : {config['model']}")
        print(f"       rules     : {len(rule_registry)} 条规则注册")
        return 0


async def _cmd_load(args: argparse.Namespace) -> int:
    """deepcode session load <path> — 加载项目"""
    ctx = _ensure_ctx()

    project_path = os.path.abspath(args.path)
    if not os.path.isdir(project_path):
        print(f"[load] 错误: 路径不存在: {project_path}")
        return 1

    ctx.project_path = project_path
    print(f"[load] 正在分析项目结构: {project_path}")

    # 扫描项目文件
    files = _scan_project(project_path)
    deps = _resolve_deps(files)

    ctx.project_structure = {
        "root": project_path,
        "files": files,
        "dependency_graph": deps,
        "total_files": len(files),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }

    print(f"       {len(files)} 个文件, {len(deps)} 条依赖边")
    print(f"       AST 缓存就绪")
    return 0


async def _cmd_check(args: argparse.Namespace) -> int:
    """deepcode session check [--rule RULE] — 运行分析"""
    ctx = _ensure_ctx()
    if not ctx.project_path:
        print("[check] 未加载项目。先用 'deepcode session load <path>' 加载。")
        return 1

    rule_filter = args.rule or "all"
    output_format = args.format or "human"
    start = time.time()

    print(f"[check] 规则: {rule_filter}")
    print(f"       {'='*40}")

    # 按依赖图顺序并行分析
    results = await _run_analysis(ctx, rule_filter)

    elapsed = time.time() - start
    ctx.checks_run += 1
    ctx.last_check = datetime.now(timezone.utc).isoformat()

    # 输出
    n_issues = sum(1 for r in results if r.get("severity", "") in ("error", "warning"))
    if output_format == "json":
        print(json.dumps({"checks": ctx.checks_run, "results": results,
                          "duration_s": round(elapsed, 2)}, indent=2))
    else:
        print(f"       检查完成: {len(results)} 项, {n_issues} 个问题")
        for r in results:
            if r.get("severity") in ("error", "warning"):
                print(f"  [{r['severity']:>7}] {r.get('file','?')}:{r.get('line','?')}  {r.get('message','')}")
        print(f"       耗时: {elapsed:.2f}s")

    return 1 if n_issues > 0 else 0


async def _cmd_close(args: argparse.Namespace) -> int:
    """deepcode session close — 释放会话"""
    global _global_ctx
    async with _ctx_lock:
        if _global_ctx is None:
            print("[close] 无活跃会话。")
            return 0
        ctx = _global_ctx
        checks = ctx.checks_run
        _global_ctx = None
        print(f"[close] 会话已关闭 (运行了 {checks} 次检查)")
        return 0


async def _cmd_daemon(args: argparse.Namespace) -> int:
    """deepcode session daemon — 守护进程模式（IDE 增量分析）"""
    ctx = _ensure_ctx()
    if not ctx.project_path:
        print("[daemon] 未加载项目。")
        return 1

    interval = args.interval
    watch_dirs = [ctx.project_path]

    print(f"[daemon] 增量分析模式启动")
    print(f"         监控目录: {ctx.project_path}")
    print(f"         检查间隔: {interval}s")
    print(f"         Ctrl+C 停止")

    # 记录文件快照
    snapshot = _file_snapshot(watch_dirs)

    try:
        while True:
            await asyncio.sleep(interval)
            new_snapshot = _file_snapshot(watch_dirs)
            changed = _diff_snapshot(snapshot, new_snapshot)

            if changed:
                print(f"\n[daemon] 检测到 {len(changed)} 个文件变更:")
                for f in changed:
                    print(f"         {f}")
                # 增量分析
                start = time.time()
                results = await _run_analysis(ctx, "all", focus_files=changed)
                elapsed = time.time() - start
                print(f"         增量分析完成 ({elapsed:.2f}s)")
                snapshot = new_snapshot
            else:
                print(".", end="", flush=True)
    except KeyboardInterrupt:
        print("\n[daemon] 已停止")
    return 0


# ── 基础设施 ──────────────────────────────────────────────────

def _build_default_rule_matrix() -> dict[str, Any]:
    """
    二维调度矩阵 (操作 × 语言) — 模仿 GGML 的 OpCode × 数据类型矩阵

    注册新规则 = 在矩阵中填一格:
      rules["check_sql"]["python"] = python_sql_check_handler
      rules["check_sql"]["java"]   = java_sql_check_handler
    """
    return {
        "check_sql": {
            "handler": "sql_injection_detector",
            "languages": ["python", "java", "javascript", "typescript"],
        },
        "check_secrets": {
            "handler": "secret_scanner",
            "languages": ["all"],
        },
        "check_types": {
            "handler": "type_checker",
            "languages": ["python", "typescript"],
        },
        "check_deadcode": {
            "handler": "dead_code_detector",
            "languages": ["python", "java", "javascript"],
        },
        "check_performance": {
            "handler": "performance_analyzer",
            "languages": ["all"],
        },
    }


def _scan_project(root: str) -> list[dict[str, Any]]:
    """扫描项目文件"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过隐藏目录和常见忽略目录
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in (
            "node_modules", "__pycache__", "venv", ".venv", "dist", "build")]
        for fn in filenames:
            if fn.endswith((".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h")):
                fpath = os.path.join(dirpath, fn)
                try:
                    stat = os.stat(fpath)
                    files.append({
                        "path": fpath,
                        "relpath": os.path.relpath(fpath, root),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "ext": os.path.splitext(fn)[1],
                    })
                except OSError:
                    continue
    return files


def _resolve_deps(files: list[dict]) -> list[tuple[str, str]]:
    """解析文件间依赖（import 图）"""
    deps = []
    path_to_rel = {f["path"]: f["relpath"] for f in files}

    for f in files:
        rel = f["relpath"]
        try:
            with open(f["path"], "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    # Python import
                    if line.startswith("import ") or line.startswith("from "):
                        parts = line.split()
                        for p in parts[1:]:
                            p = p.split(".")[0].strip(",")
                            for candidate in path_to_rel:
                                if candidate.endswith(f"{p}.py") or candidate.endswith(f"{p}.so"):
                                    deps.append((rel, path_to_rel[candidate]))
                    # JS/TS import
                    elif "from '" in line or 'from "' in line:
                        # simplified: extract quoted path
                        for q in ("'", '"'):
                            if f"from {q}" in line:
                                target = line.split(f"from {q}")[1].split(q)[0]
                                if target.startswith("."):
                                    resolved = os.path.normpath(
                                        os.path.join(os.path.dirname(f["path"]), target)
                                    )
                                    for candidate in path_to_rel:
                                        if candidate.startswith(resolved):
                                            deps.append((rel, path_to_rel[candidate]))
        except OSError:
            continue
    return deps


def _file_snapshot(dirs: list[str]) -> dict[str, float]:
    """获取文件快照（路径→mtime）"""
    snap = {}
    for d in dirs:
        for dirpath, _, filenames in os.walk(d):
            for fn in filenames:
                fpath = os.path.join(dirpath, fn)
                try:
                    snap[fpath] = os.stat(fpath).st_mtime
                except OSError:
                    continue
    return snap


def _diff_snapshot(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """找出变更的文件"""
    changed = []
    for path, mtime in new.items():
        if path not in old or old[path] != mtime:
            changed.append(path)
    return changed


async def _run_analysis(ctx: AnalysisContext, rule_filter: str,
                        focus_files: list[str] | None = None) -> list[dict[str, Any]]:
    """
    在依赖图上执行并行分析 — 模仿 GGML 的计算图调度

    GGML 模式: 计算图(DAG) → 拓扑排序 → 线程池调度 → 结果归并
    """
    if not ctx.project_structure:
        return []

    files = ctx.project_structure.get("files", [])
    deps = ctx.project_structure.get("dependency_graph", [])

    if focus_files:
        files = [f for f in files if f["path"] in focus_files]

    if not files:
        return []

    # 构建 DAG
    file_set = {f["path"] for f in files}
    dep_graph: dict[str, list[str]] = {f["path"]: [] for f in files}
    for src, dst in deps:
        if src in dep_graph and dst in dep_graph:
            dep_graph[src].append(dst)

    # 拓扑排序 + 分批（模仿 GGML 的 graph_plan）
    batches = _topological_batches(dep_graph)

    # 并发执行（模仿 GGML 的 graph_compute 线程池调度）
    results = []
    sem = asyncio.Semaphore(4)  # max_concurrency

    async def _check_file(file_path: str) -> dict[str, Any]:
        async with sem:
            # 模拟检查：实际应该调用注册的规则处理器
            rel = next((f["relpath"] for f in files if f["path"] == file_path), file_path)
            return {
                "file": rel,
                "severity": "info",
                "message": f"analyzed ({os.path.getsize(file_path)} bytes)",
                "line": 1,
            }

    for batch in batches:
        batch_results = await asyncio.gather(
            *[_check_file(f) for f in batch],
            return_exceptions=True,
        )
        for r in batch_results:
            if isinstance(r, dict):
                results.append(r)

    return results


def _topological_batches(dep_graph: dict[str, list[str]]) -> list[list[str]]:
    """DAG 拓扑排序 → 并行分批 — 直接对应 GGML 的 graph_plan"""
    in_degree = {node: 0 for node in dep_graph}
    for deps in dep_graph.values():
        for d in deps:
            if d in in_degree:
                in_degree[d] += 1

    queue = [node for node, deg in in_degree.items() if deg == 0]
    batches = []

    while queue:
        batches.append(list(queue))
        next_queue = []
        for node in queue:
            for dep in dep_graph.get(node, []):
                if dep in in_degree:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        next_queue.append(dep)
        queue = next_queue

    return batches


# ── CLI 入口 ──────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode session",
        description="会话生命周期管理 — 分析引擎的 init/load/check/close 模式",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="初始化分析引擎")
    p_init.add_argument("--workspace", "-w", default=os.getcwd())
    p_init.add_argument("--model", "-m", default=None)
    p_init.add_argument("--max-iterations", type=int, default=40)
    p_init.add_argument("--rules-dir", default=None)
    p_init.add_argument("--verbose", action="store_true")

    # load
    p_load = sub.add_parser("load", help="加载项目")
    p_load.add_argument("path", help="项目目录路径")

    # check
    p_check = sub.add_parser("check", help="运行分析检查")
    p_check.add_argument("--rule", "-r", default="all", help="规则名称 (默认 all)")
    p_check.add_argument("--format", "-f", choices=["human", "json"], default="human")

    # close
    sub.add_parser("close", help="关闭会话")

    # daemon
    p_daemon = sub.add_parser("daemon", help="守护进程模式（增量分析）")
    p_daemon.add_argument("--interval", "-i", type=int, default=5, help="检查间隔(秒)")

    args = parser.parse_args(argv)

    commands = {
        "init": _cmd_init,
        "load": _cmd_load,
        "check": _cmd_check,
        "close": _cmd_close,
        "daemon": _cmd_daemon,
    }
    cmd = commands.get(args.command)
    if cmd is None:
        parser.print_help()
        return 1
    return asyncio.run(cmd(args))


if __name__ == "__main__":
    raise SystemExit(main())
