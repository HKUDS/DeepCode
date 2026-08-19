"""Guard-event telemetry adapter (P5) — guard_event_callback → deepcode-telemetry.

REASONIX 守卫事件（``blocked`` / ``injection`` / ``tool_block``）的可选遥测接入。
默认零开销：未设置 ``DEEPCODE_GUARD_TELEMETRY=1`` 时不做任何事；遥测 skill 缺失或
调用失败时静默降级，绝不影响 agent 主循环。

设计要点
--------
- 纯 stdlib，不依赖 loguru / core 内部（避免循环导入与回归风险）。
- 通过 ``importlib.util.spec_from_file_location`` 动态加载
  ``.deepcode/skills/deepcode-telemetry/telemetry.py``，不污染 ``sys.path``。
- 模块级缓存：每个进程只尝试加载一次（``_attempted`` / ``_callbacks_cache``）。
- 事件映射（kind → 遥测调用）：
    - ``blocked``    → increment_counter("guard.blocked") + record_metric("guard.blocked.streak")
    - ``injection``  → increment_counter("guard.injections")
    - ``tool_block`` → increment_counter("guard.tool_block") + 按工具细分计数器
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Callable

ENV_GUARD_TELEMETRY = "DEEPCODE_GUARD_TELEMETRY"

# core/loop/guard_telemetry.py → parents[2] = F:/DEEPCODE
_TELEMETRY_SKILL_REL = Path(".deepcode") / "skills" / "deepcode-telemetry" / "telemetry.py"

_attempted = False
_callbacks_cache: Callable[[dict], None] | None = None


def _load_telemetry_module() -> Any | None:
    """动态加载 deepcode-telemetry skill 模块；失败返回 None（静默降级）。"""
    skill_py = Path(__file__).resolve().parents[2] / _TELEMETRY_SKILL_REL
    if not skill_py.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_deepcode_guard_telemetry", skill_py)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _build_callback(engine: Any) -> Callable[[dict], None]:
    """构造守卫事件 → 遥测调用回调。所有遥测调用异常静默吞掉。"""

    def on_guard_event(event: dict) -> None:
        try:
            kind = event.get("kind")
            if kind == "blocked":
                engine.increment_counter("guard.blocked", 1)
                streak = event.get("streak")
                if streak is not None:
                    engine.record_metric("guard.blocked.streak", float(streak))
            elif kind == "injection":
                engine.increment_counter("guard.injections", 1)
            elif kind == "tool_block":
                engine.increment_counter("guard.tool_block", 1)
                tool = event.get("tool")
                if tool:
                    engine.increment_counter(f"guard.tool_block.{tool}", 1)
        except Exception:
            pass  # telemetry 失败静默降级，绝不影响 agent 主循环

    return on_guard_event


def make_telemetry_guard_callback() -> Callable[[dict], None] | None:
    """惰性构造遥测守卫回调（进程级缓存，只尝试一次）。"""
    global _attempted, _callbacks_cache
    if _attempted:
        return _callbacks_cache
    _attempted = True
    try:
        module = _load_telemetry_module()
        if module is None or not hasattr(module, "get_telemetry"):
            _callbacks_cache = None
            return None
        _callbacks_cache = _build_callback(module.get_telemetry())
    except Exception:
        _callbacks_cache = None
    return _callbacks_cache


def install_guard_telemetry(spec: Any) -> None:
    """runner ``run()`` 开头调用：可选装配遥测回调，默认零开销。

    - 已有 ``guard_event_callback`` → 不覆盖。
    - ``DEEPCODE_GUARD_TELEMETRY`` 未设为 "1" → 不装配。
    - 遥测 skill 不可用 → 静默保持 None。
    """
    if spec.guard_event_callback is not None:
        return
    if os.environ.get(ENV_GUARD_TELEMETRY, "0") != "1":
        return
    callback = make_telemetry_guard_callback()
    if callback is not None:
        spec.guard_event_callback = callback


__all__ = [
    "ENV_GUARD_TELEMETRY",
    "make_telemetry_guard_callback",
    "install_guard_telemetry",
]
