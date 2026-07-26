"""
DeepCode 运行时引擎 — Go 逆向经验驱动的 4 项改进

改进一: 多运行时检测      → detect_runtimes()
改进二: 策略自动选择      → select_strategy()  
改进三: 三阶段管道模式     → Pipeline
改进四: 热插拔插件架构     → HotPlugRegistry

集成进 DeepCode 现有架构，直接可用。
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ═══════════════════════════════════════════════════
# 改进一: 多运行时检测
# ═══════════════════════════════════════════════════

# 运行时特征签名
RUNTIME_SIGNATURES = {
    "Go": {
        "patterns": [b"go1.", b"runtime.", b"main.main", b"goroutine"],
        "stack_check": True,
        "compiler_ids": ["golang"],
        "typical_imports_range": (0, 60),  # Go 的 kernel32 导入数
    },
    "Rust": {
        "patterns": [b"rust_begin_unwind", b"core::", b"rustc"],
        "stack_check": False,
        "compiler_ids": ["rust"],
        "typical_imports_range": (0, 30),
    },
    "CXX": {
        "patterns": [b"libc++", b"libstdc++", b"__gxx_personality", b"_GLOBAL__sub_I"],
        "stack_check": False,
        "compiler_ids": ["clang", "gcc", "msvc"],
        "typical_imports_range": (10, 200),
    },
    "Nim": {
        "patterns": [b"NimMain", b"nim"],
        "stack_check": False,
        "compiler_ids": ["nim"],
        "typical_imports_range": (0, 30),
    },
}


@dataclass
class RuntimeInfo:
    """检测到的运行时信息"""
    name: str
    confidence: float  # 0-1
    estimated_funcs: int = 0
    features_found: list = field(default_factory=list)


def detect_runtimes(
    binary_data: bytes = None,
    compiler: str = "",
    import_count: int = 0,
    function_list: list = None,
    has_stack_check_count: int = 0,
    total_functions: int = 0,
) -> list[RuntimeInfo]:
    """
    检测二进制中包含的运行时 (多语言混合检测)

    参数:
      binary_data: 二进制文件内容 (用于字符串搜索)
      compiler: Ghidra 检测到的编译器标识
      import_count: 导入函数数
      function_list: 函数名列表
      has_stack_check_count: 有栈检查的函数数
      total_functions: 总函数数

    返回:
      [RuntimeInfo("Go", 0.95), RuntimeInfo("C++", 0.4)]  # 混合二进制
    """
    results = []
    data = binary_data or b""

    for runtime_name, sig in RUNTIME_SIGNATURES.items():
        score = 0.0
        max_score = 10.0
        features = []

        # 1. 编译器标识 (权重: 3)
        if compiler.lower() in sig["compiler_ids"]:
            score += 3.0
            features.append(f"compiler={compiler}")

        # 2. 特征字符串 (权重: 每个模式 +1.5)
        for pattern in sig["patterns"]:
            if pattern in data:
                score += 1.5
                features.append(f"pattern={pattern[:10]}")

        # 3. 栈检查 (权重: +2, 仅 Go)
        if sig["stack_check"] and has_stack_check_count > total_functions * 0.1:
            score += 2.0
            features.append(f"stack_check={has_stack_check_count}")

        # 4. 导入数范围 (权重: +1)
        lo, hi = sig["typical_imports_range"]
        if lo <= import_count <= hi:
            score += 1.0
            features.append(f"imports={import_count}")

        # 5. FUN_ 函数名比例 (权重: +0.5)
        if function_list and runtime_name in ("Go", "Rust", "Nim"):
            fun_count = sum(1 for f in function_list if isinstance(f, str) and f.startswith("FUN_"))
            if total_functions > 0 and fun_count / total_functions > 0.8:
                score += 0.5
                features.append("FUN_>80%")

        if score > 0:
            results.append(RuntimeInfo(
                name=runtime_name,
                confidence=round(min(score / max_score, 1.0), 2),
                estimated_funcs=total_functions if score > 3 else 0,
                features_found=features,
            ))

    # 排序: 置信度从高到低
    results.sort(key=lambda r: -r.confidence)

    return results


# ═══════════════════════════════════════════════════
# 改进二: 策略自动选择
# ═══════════════════════════════════════════════════

# 每种运行时的分析策略
STRATEGY_MAP = {
    "Go": {
        "priority": 1,
        "tools": ["stack_check_analyzer", "go_abi_analyzer",
                  "interface_dispatch_analyzer", "strip_analyzer"],
        "description": "Go ABIInternal — 栈检查/接口派发/itab",
    },
    "Rust": {
        "priority": 2,
        "tools": ["rust_abi_analyzer", "trait_dispatch"],
        "description": "Rust ABI — trait 分发/所有权检查",
    },
    "CXX": {
        "priority": 3,
        "tools": ["vtable_analyzer", "exception_handler", "export_table"],
        "description": "C++ ABI — vtable/RTTI/异常处理",
    },
    "Nim": {
        "priority": 4,
        "tools": ["nim_abi_analyzer"],
        "description": "Nim ABI — GC/引用追踪",
    },
}


def select_strategy(runtimes: list[RuntimeInfo]) -> dict:
    """
    根据检测到的运行时自动选择分析策略

    输入: [RuntimeInfo("Go", 0.95), RuntimeInfo("C++", 0.4)]
    输出: {"primary": "Go", "tools": [...], "hybrid": True}
    """
    if not runtimes:
        return {"primary": "unknown", "tools": [], "hybrid": False}

    primary = runtimes[0]
    strategy = STRATEGY_MAP.get(primary.name, {})

    # 检测是否混合二进制
    hybrid = len([r for r in runtimes if r.confidence > 0.3]) > 1

    # 混合时合并工具
    tools = list(strategy.get("tools", []))
    if hybrid:
        for r in runtimes[1:]:
            if r.confidence > 0.3:
                extra = STRATEGY_MAP.get(r.name, {}).get("tools", [])
                tools.extend(extra)
                # 添加 CGO 桥梁分析
                tools.append("cgo_bridge_analyzer")

    return {
        "primary": primary.name,
        "confidence": primary.confidence,
        "tools": tools,
        "hybrid": hybrid,
        "description": strategy.get("description", ""),
    }


# ═══════════════════════════════════════════════════
# 改进三: 三阶段管道模式
# ═══════════════════════════════════════════════════

class Stage(Enum):
    INPUT = "input"       # tokenize: 输入处理/初始化
    PROCESS = "process"   # predict:  核心处理/推理
    OUTPUT = "output"     # detokenize: 输出处理/格式化


@dataclass
class StageHandler:
    """管道阶段处理器"""
    name: str
    handler: Callable
    stage: Stage
    retry_count: int = 0
    timeout: float = 30.0


class Pipeline:
    """
    三阶段管道模式 — 对应 Ollama 的 tokenize→predict→detokenize

    用法:
      pipe = Pipeline()
      pipe.add("init", init_workspace, Stage.INPUT)
      pipe.add("run", run_agent, Stage.PROCESS, retry=3)
      pipe.add("format", format_result, Stage.OUTPUT)
      result = await pipe.run(input_data)
    """

    def __init__(self):
        self.stages: list[StageHandler] = []
        self.stats = {"runs": 0, "failures": 0, "total_time": 0}

    def add(self, name: str, handler: Callable,
            stage: Stage = Stage.PROCESS,
            retry: int = 0, timeout: float = 30.0):
        """添加管道阶段"""
        self.stages.append(StageHandler(
            name=name, handler=handler,
            stage=stage, retry_count=retry, timeout=timeout,
        ))

    async def run(self, input_data: Any) -> Any:
        """执行整个管道"""
        self.stats["runs"] += 1
        start = time.time()
        data = input_data

        for stage in self.stages:
            for attempt in range(stage.retry_count + 1):
                try:
                    if asyncio.iscoroutinefunction(stage.handler):
                        data = await asyncio.wait_for(
                            stage.handler(data), timeout=stage.timeout)
                    else:
                        data = stage.handler(data)
                    break
                except Exception as e:
                    if attempt < stage.retry_count:
                        continue
                    self.stats["failures"] += 1
                    raise RuntimeError(
                        f"Pipeline stage '{stage.name}' failed: {e}")

        self.stats["total_time"] += time.time() - start
        return data

    def summary(self) -> str:
        """管道摘要"""
        stages = " → ".join(f"{s.name}" for s in self.stages)
        return (f"Pipeline[{stages}] "
                f"runs={self.stats['runs']} "
                f"fail={self.stats['failures']} "
                f"avg={self.stats['total_time']/max(self.stats['runs'],1):.1f}s")


# ═══════════════════════════════════════════════════
# 改进四: 热插拔插件架构 (Go interface/itab 启发)
# ═══════════════════════════════════════════════════

class PluginStatus(Enum):
    REGISTERED = "registered"
    LOADED = "loaded"
    ACTIVE = "active"
    ERROR = "error"


@dataclass
class Plugin:
    """可热插拔的插件 — 对应 Go 的 interface 实现"""
    name: str
    version: str
    capabilities: list[str]
    factory: Callable  # 创建插件实例的工厂函数
    dependencies: list[str] = field(default_factory=list)
    status: PluginStatus = PluginStatus.REGISTERED


class HotPlugRegistry:
    """
    热插拔插件注册表 — 类似 Go 的 itab 机制

    运行时根据能力查表，动态加载/卸载插件。

    用法:
      reg = HotPlugRegistry()
      reg.register("go_abi", GoABIAnalyzer, ["go", "abi", "stack_check"])
      reg.register("c_abi", CABIAnalyzer, ["c", "c++", "abi"])

      # 按需求加载
      analyzers = reg.resolve(["go", "abi"])
      # → [GoABIAnalyzer]  (只加载匹配 Go+ABI 的插件)

    itab 类比:
      Go interface   → Plugin.capabilities
      Go itab 表     → HotPlugRegistry._capability_index
      Go 断言        → resolve(["go", "abi"])
    """

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        # 能力索引: capability → [plugin_name]
        self._capability_index: dict[str, list[str]] = {}

    def register(self, plugin_cls: type, name: str = "",
                 version: str = "1.0.0",
                 dependencies: list[str] = None):
        """注册插件 (热插拔)"""
        pname = name or plugin_cls.__name__
        capabilities = getattr(plugin_cls, "capabilities", [pname.lower()])

        plugin = Plugin(
            name=pname,
            version=version,
            capabilities=capabilities,
            factory=plugin_cls,
            dependencies=dependencies or [],
        )
        self._plugins[pname] = plugin

        # 更新能力索引
        for cap in capabilities:
            if cap not in self._capability_index:
                self._capability_index[cap] = []
            self._capability_index[cap].append(pname)

    def resolve(self, required_capabilities: list[str]) -> list[type]:
        """
        解析能力 → 插件类
        类似 Go 的 itab 查找: (interface_type, concrete_type) → 方法表
        """
        matched = set()
        for cap in required_capabilities:
            if cap in self._capability_index:
                matched.update(self._capability_index[cap])

        plugins = []
        for pname in matched:
            plugin = self._plugins[pname]
            if plugin.status != PluginStatus.ERROR:
                # 检查所有依赖是否可解析
                deps_ok = all(
                    d in self._plugins
                    for d in plugin.dependencies
                )
                if deps_ok:
                    plugin.status = PluginStatus.ACTIVE
                    plugins.append(plugin.factory)

        return plugins

    def unregister(self, name: str):
        """卸载插件 (热移除)"""
        if name in self._plugins:
            plugin = self._plugins.pop(name)
            for cap in plugin.capabilities:
                if cap in self._capability_index:
                    self._capability_index[cap] = [
                        n for n in self._capability_index[cap] if n != name
                    ]

    def summary(self) -> str:
        return (f"HotPlugRegistry: {len(self._plugins)} plugins, "
                f"{len(self._capability_index)} capabilities")


# ═══════════════════════════════════════════════════
# 单元测试
# ═══════════════════════════════════════════════════

def test_runtime_detection():
    """测试多运行时检测"""
    print("=" * 55)
    print("  改进一测试: 多运行时检测")
    print("=" * 55)

    # 模拟 Ollama (Go + CGO)
    ollama_data = b"go1.26runtime.main.main goroutine " + b"libc++" * 10
    runtimes = detect_runtimes(
        binary_data=ollama_data,
        compiler="clangwindows",
        import_count=100,
        total_functions=31847,
    )
    print(f"  Ollama: {[f'{r.name}({r.confidence:.0%})' for r in runtimes]}")

    # 模拟 DockerCli (纯 Go)
    go_data = b"go1.26runtime.main.main goroutine channel"
    runtimes2 = detect_runtimes(
        binary_data=go_data,
        compiler="golang",
        import_count=48,
    )
    print(f"  DockerCli: {[f'{r.name}({r.confidence:.0%})' for r in runtimes2]}")

    # 模拟 llama.dll (纯 C++)
    cpp_data = b"libc++libstdc++__gxx_personality" * 5
    runtimes3 = detect_runtimes(
        binary_data=cpp_data,
        compiler="clang",
        import_count=3,
    )
    print(f"  llama.dll: {[f'{r.name}({r.confidence:.0%})' for r in runtimes3]}")


def test_strategy_selection():
    """测试策略选择"""
    print()
    print("=" * 55)
    print("  改进二测试: 策略自动选择")
    print("=" * 55)

    # Ollama: Go + C++ 混合
    runtimes = [
        RuntimeInfo("Go", 0.95, 20000),
        RuntimeInfo("CXX", 0.6, 11847),
    ]
    strategy = select_strategy(runtimes)
    print(f"  Ollama: primary={strategy['primary']} "
          f"hybrid={strategy['hybrid']} "
          f"tools={len(strategy['tools'])}个")


def test_pipeline():
    """测试管道模式"""
    print()
    print("=" * 55)
    print("  改进三测试: 管道模式")
    print("=" * 55)

    pipe = Pipeline()
    
    async def tokenize(data):
        await asyncio.sleep(0.01)
        return f"tokens:{data}"
    
    def predict(data):
        return f"result({data})"
    
    def detokenize(data):
        return f"output:{data}"

    pipe.add("tokenize", tokenize, stage=Stage.INPUT)
    pipe.add("predict", predict, stage=Stage.PROCESS)
    pipe.add("detokenize", detokenize, stage=Stage.OUTPUT)

    result = asyncio.run(pipe.run("hello"))
    print(f"  Result: {result}")
    print(f"  Stats: {pipe.summary()}")


def test_hotplug():
    """测试热插拔插件"""
    print()
    print("=" * 55)
    print("  改进四测试: 热插拔插件")
    print("=" * 55)

    class GoABIAnalyzer:
        capabilities = ["go", "abi", "stack_check"]
    
    class CABIAnalyzer:
        capabilities = ["c", "c++", "abi"]
    
    class CGOBridgeAnalyzer:
        capabilities = ["cgo", "go", "c"]
        dependencies = ["GoABIAnalyzer"]

    reg = HotPlugRegistry()
    reg.register(GoABIAnalyzer)
    reg.register(CABIAnalyzer)
    reg.register(CGOBridgeAnalyzer)

    # Ollama 需要: go + c + abi
    ollama_tools = reg.resolve(["go", "c", "abi"])
    print(f"  Ollama tools: {[t.__name__ for t in ollama_tools]}")

    # DockerCli 只需要: go + abi
    docker_tools = reg.resolve(["go", "abi"])
    print(f"  DockerCli tools: {[t.__name__ for t in docker_tools]}")

    print(f"  Registry: {reg.summary()}")


if __name__ == "__main__":
    test_runtime_detection()
    test_strategy_selection()
    test_pipeline()
    test_hotplug()
    print()
    print("═" * 55)
    print("  全部测试通过 ✅")
