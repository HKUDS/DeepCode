#!/usr/bin/env python3
"""
ParallelToolExecutor v2.0 — DAG 并行工具执行引擎

升级: 模仿 GGML 的 graph_plan + graph_compute + 线程池模式

关键改进 (基于 llama.cpp 逆向发现):
  1. DAG 任务图 (+ 拓扑排序)           → 对应 GGML graph_plan
  2. 无锁就绪队列 + 线程池调度          → 对应 GGML graph_compute
  3. 单线程 fast path                  → 对应 GGML 单线程路径
  4. 二维调度矩阵 (操作 × 数据类型)     → 对应 GGML OpCode × 数据类型矩阵
  5. 依赖感知的并行分批                 → 对应 GGML 图分割
"""

import asyncio
import time
import json
from typing import Any, Callable, Dict, List, Optional, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from collections import deque


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class TaskPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


# ── 二维调度矩阵 (模仿 GGML 的 OpCode × 数据类型) ────────────

class MatrixToolRegistry:
    """
    二维调度矩阵: (操作类型 × 语言/数据类型) → 处理函数

    注册方式:
      registry = MatrixToolRegistry()
      registry.register("check_sql", "python", python_sql_checker)
      registry.register("check_sql", "java", java_sql_checker)

    调度方式:
      handler = registry.lookup("check_sql", "python")  # O(1) 查表
    """

    def __init__(self):
        # 二维矩阵: matrix[op][variant] = handler
        self._matrix: dict[str, dict[str, Callable]] = {}
        # 默认处理器: matrix[op]["*"] = default_handler
        self._defaults: dict[str, Callable] = {}

    def register(self, operation: str, variant: str, handler: Callable):
        """注册处理器到矩阵的 (operation, variant) 位置"""
        if operation not in self._matrix:
            self._matrix[operation] = {}
        self._matrix[operation][variant] = handler

    def register_default(self, operation: str, handler: Callable):
        """注册默认处理器 (当 variant 无匹配时使用)"""
        self._defaults[operation] = handler

    def lookup(self, operation: str, variant: str) -> Callable | None:
        """O(1) 查表: 精确匹配 → 默认匹配 → None"""
        ops = self._matrix.get(operation, {})
        if variant in ops:
            return ops[variant]
        if "*" in ops:
            return ops["*"]
        return self._defaults.get(operation)

    @property
    def operations(self) -> list[str]:
        return list(self._matrix.keys())

    def stats(self) -> dict:
        return {
            "operations": len(self._matrix),
            "total_entries": sum(len(v) for v in self._matrix.values()),
        }


# ── DAG 任务模型 ─────────────────────────────────────────────

@dataclass
class DAGNode:
    """计算图节点 — 对应 GGML 计算图中的一个操作节点"""
    id: str
    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    variant: str = "*"          # 数据类型/语言变体
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: List[str] = field(default_factory=list)  # 依赖的节点 ID 列表
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    start_time: float = 0
    end_time: float = 0
    retries: int = 0
    max_retries: int = 3

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time > 0 else 0


class DAGExecutor:
    """
    DAG 任务执行引擎 — 对应 GGML 的 graph_plan + graph_compute

    模式匹配:
      GGML graph_plan      → self._build_execution_plan()
      GGML graph_compute   → self._execute_plan()
      GGML 单线程 fast path → self._execute_fast_path()
      GGML 线程池           → self._execute_parallel()
    """

    def __init__(self, registry: MatrixToolRegistry | None = None,
                 max_concurrency: int = 4):
        self.registry = registry or MatrixToolRegistry()
        self.max_concurrency = max_concurrency
        self.stats = {
            "total_executions": 0,
            "dag_executions": 0,
            "fast_path_executions": 0,
            "total_nodes": 0,
            "total_time_saved_ms": 0,
        }

    # ── 构建 DAG 执行计划 (对应 GGML graph_plan) ──────────────

    def _build_execution_plan(self, nodes: List[DAGNode]) -> List[List[DAGNode]]:
        """
        拓扑排序 → 并行分批

        返回: [[batch_1], [batch_2], ...]
        同批次内无依赖冲突，可并行执行
        """
        node_map = {n.id: n for n in nodes}
        in_degree = {n.id: 0 for n in nodes}
        for n in nodes:
            for d in n.depends_on:
                if d in in_degree:
                    in_degree[d] += 1

        # 优先队列: 同批次内按优先级排序
        ready = deque(
            sorted(
                [n for n in nodes if in_degree[n.id] == 0],
                key=lambda n: n.priority.value,
                reverse=True,
            )
        )
        remaining = {n.id for n in nodes}
        plan = []

        while ready or remaining:
            batch = []
            next_ready = deque()

            while ready:
                n = ready.popleft()
                batch.append(n)
                remaining.discard(n.id)

            # 更新入度
            for n in batch:
                for d_id in remaining:
                    d = node_map[d_id]
                    if n.id in d.depends_on:
                        in_degree[d_id] -= 1
                        if in_degree[d_id] == 0:
                            next_ready.append(d)

            if batch:
                plan.append(batch)

            # 死锁检测: 如果还有剩余节点但没有就绪的，强制取一个
            if not next_ready and remaining:
                forced = next(iter(remaining))
                next_ready.append(node_map[forced])

            ready = next_ready

        return plan

    # ── 快速路径 (单线程, 对应 GGML 单线程模式) ──────────────

    async def _execute_fast_path(self, nodes: List[DAGNode]) -> List[DAGNode]:
        """单线程按序执行 — 无锁开销"""
        for n in nodes:
            n.start_time = time.time()
            try:
                n.result = await self._run_node(n)
                n.status = TaskStatus.DONE
            except Exception as e:
                n.status = TaskStatus.ERROR
                n.error = str(e)
            n.end_time = time.time()
        return nodes

    # ── 并行路径 (多线程, 对应 GGML 多线程模式) ──────────────

    async def _execute_plan(self, plan: List[List[DAGNode]]) -> List[DAGNode]:
        """逐批执行，同批次并行 (对应 GGML 线程池 + 任务队列)"""
        sem = asyncio.Semaphore(self.max_concurrency)
        done_nodes = []

        for batch in plan:
            async def run_node(n: DAGNode) -> DAGNode:
                async with sem:
                    n.start_time = time.time()
                    try:
                        n.result = await self._run_node(n)
                        n.status = TaskStatus.DONE
                    except Exception as e:
                        n.status = TaskStatus.ERROR
                        n.error = str(e)
                    n.end_time = time.time()
                    return n

            # 同批次并行
            results = await asyncio.gather(
                *[run_node(n) for n in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, DAGNode):
                    done_nodes.append(r)

        return done_nodes

    # ── 核心执行方法 ─────────────────────────────────────────

    async def execute(self, nodes: List[DAGNode]) -> List[DAGNode]:
        """
        执行 DAG — 自动选择路径:
          - 1 个节点 → 直接执行
          - 1 个批次 → 并行执行
          - 多批次   → 逐批并行
        """
        self.stats["total_executions"] += 1

        if len(nodes) == 1:
            # 快速路径: 单节点直接执行 (对应 GGML 单线程)
            self.stats["fast_path_executions"] += 1
            n = nodes[0]
            n.start_time = time.time()
            try:
                n.result = await self._run_node(n)
                n.status = TaskStatus.DONE
            except Exception as e:
                n.status = TaskStatus.ERROR
                n.error = str(e)
            n.end_time = time.time()
            return [n]

        # 构建执行计划
        self.stats["dag_executions"] += 1
        plan = self._build_execution_plan(nodes)
        done = await self._execute_plan(plan)
        self.stats["total_nodes"] += len(nodes)
        return done

    async def execute_from_dicts(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """字典列表接口 — 兼容 v1.0 接口"""
        nodes = []
        for i, t in enumerate(tasks):
            nodes.append(DAGNode(
                id=t.get("id", str(i + 1)),
                tool_name=t["tool"],
                args=t.get("args", {}),
                variant=t.get("variant", "*"),
                priority=TaskPriority[t.get("priority", "NORMAL").upper()],
                depends_on=t.get("depends_on", []),
            ))
        results = await self.execute(nodes)
        return [self._format_node(n) for n in results]

    async def execute_streaming(
        self, nodes: List[DAGNode]
    ) -> AsyncGenerator[Dict, None]:
        """流式执行 — 每完成一个节点就 yield"""
        plan = self._build_execution_plan(nodes)

        for batch in plan:
            pending = [self._run_and_format(n) for n in batch]
            for coro in asyncio.as_completed(pending):
                yield await coro

    async def _run_node(self, node: DAGNode) -> Any:
        """执行单个节点 — 通过 2D 矩阵查找处理器"""
        handler = self.registry.lookup(node.tool_name, node.variant)

        if handler is not None:
            if asyncio.iscoroutinefunction(handler):
                return await handler(**node.args)
            else:
                return handler(**node.args)

        # 兜底: 未注册的工具
        return {"note": f"Tool '{node.tool_name}/{node.variant}' not registered",
                "args": node.args}

    async def _run_and_format(self, node: DAGNode) -> Dict:
        try:
            node.start_time = time.time()
            node.result = await self._run_node(node)
            node.status = TaskStatus.DONE
        except Exception as e:
            node.status = TaskStatus.ERROR
            node.error = str(e)
        node.end_time = time.time()
        return self._format_node(node)

    def _format_node(self, node: DAGNode) -> Dict:
        return {
            "task_id": node.id,
            "tool": node.tool_name,
            "variant": node.variant,
            "status": node.status.value,
            "result": node.result,
            "error": node.error,
            "duration_ms": round(node.duration * 1000, 1),
        }

    def get_stats(self) -> Dict:
        return {**self.stats, "registry": self.registry.stats()}


# ── 便捷全局接口 ──────────────────────────────────────────────

_executor: Optional[DAGExecutor] = None
_registry: Optional[MatrixToolRegistry] = None


def get_registry() -> MatrixToolRegistry:
    global _registry
    if _registry is None:
        _registry = MatrixToolRegistry()
    return _registry


def get_executor(**kwargs) -> DAGExecutor:
    global _executor
    if _executor is None:
        reg = get_registry()
        _executor = DAGExecutor(registry=reg, **kwargs)
    return _executor


async def run_parallel(tasks: List[Dict]) -> List[Dict]:
    """兼容 v1.0 接口"""
    executor = get_executor()
    return await executor.execute_from_dicts(tasks)


# ── 测试 ──────────────────────────────────────────────────────

async def _test():
    """测试 DAG 并行执行"""
    async def mock_fetch(ts_code=None, **kw):
        await asyncio.sleep(0.3)
        return {"ts_code": ts_code, "close": 100}

    async def mock_analyze(data=None, **kw):
        await asyncio.sleep(0.2)
        return {"analysis": "done", "data": data}

    # 注册 2D 矩阵
    reg = get_registry()
    reg.register("fetch_data", "stock", mock_fetch)
    reg.register("fetch_data", "index", mock_fetch)
    reg.register("analyze", "*", mock_analyze)

    executor = DAGExecutor(registry=reg, max_concurrency=4)

    tasks = [
        {"id": "1", "tool": "fetch_data", "variant": "stock",
         "args": {"ts_code": "600519.SH"}},
        {"id": "2", "tool": "fetch_data", "variant": "stock",
         "args": {"ts_code": "000001.SZ"}},
        {"id": "3", "tool": "fetch_data", "variant": "index",
         "args": {"ts_code": "000001.SH"}},
        {"id": "4", "tool": "analyze", "variant": "stock",
         "args": {"data": "result_1"}, "depends_on": ["1", "2"]},
        {"id": "5", "tool": "analyze", "variant": "index",
         "args": {"data": "result_3"}, "depends_on": ["3"]},
    ]

    start = time.time()
    results = await executor.execute_from_dicts(tasks)
    elapsed = time.time() - start

    print(f"=== DAG Executor Test ===")
    print(f"5 tasks (2 parallel + 3 dependent) in {elapsed:.2f}s")
    for r in results:
        dep = " ⚡dep" if r["task_id"] in ("4", "5") else ""
        print(f"  [{r['task_id']}] {r['tool']}/{r['variant']}: {r['status']} ({r['duration_ms']}ms){dep}")
    print(f"Stats: {json.dumps(executor.get_stats(), indent=2)}")
    print(f"(vs sequential ~1.5s, saved ~{1.5 - elapsed:.1f}s)")


if __name__ == "__main__":
    asyncio.run(_test())
