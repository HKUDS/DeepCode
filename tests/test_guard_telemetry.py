"""Tests for the P5 guard telemetry adapter (guard_event_callback → deepcode-telemetry).

Covers the optional telemetry wiring implemented in
:mod:`core.loop.guard_telemetry`:

- Event mapping: ``blocked`` / ``injection`` / ``tool_block`` events drive the
  correct counters/metrics on the telemetry engine.
- Silent degradation: when the telemetry skill is unavailable or import fails,
  ``make_telemetry_guard_callback`` returns ``None`` and nothing breaks.
- Environment gate: telemetry is only assembled when
  ``DEEPCODE_GUARD_TELEMETRY=1`` and no explicit callback is set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.guard_telemetry import (  # noqa: E402
    ENV_GUARD_TELEMETRY,
    install_guard_telemetry,
    make_telemetry_guard_callback,
)


class _FakeEngine:
    """Minimal fake telemetry engine recording counter/metric calls."""

    def __init__(self) -> None:
        self.counters: list[tuple[str, float]] = []
        self.metrics: list[tuple[str, float]] = []

    def increment_counter(self, name: str, delta: float = 1.0) -> None:
        self.counters.append((name, delta))

    def record_metric(self, name: str, value: float, unit: str = "") -> None:
        self.metrics.append((name, value))


def _fake_module(engine: _FakeEngine):
    """Build a fake telemetry module exposing get_telemetry()."""
    import types

    mod = types.ModuleType("_fake_telemetry")
    mod.get_telemetry = lambda: engine
    return mod


# -- 事件映射 ------------------------------------------------------------------


def test_blocked_event_maps_to_counter_and_streak_metric(monkeypatch):
    engine = _FakeEngine()
    module = _fake_module(engine)
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: module
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    callback = make_telemetry_guard_callback()
    assert callback is not None
    callback({"kind": "blocked", "tool": None, "level": 3, "streak": 6,
              "message": "no new evidence"})

    assert engine.counters == [("guard.blocked", 1.0)]
    assert engine.metrics == [("guard.blocked.streak", 6.0)]


def test_injection_event_maps_to_counter(monkeypatch):
    engine = _FakeEngine()
    module = _fake_module(engine)
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: module
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    callback = make_telemetry_guard_callback()
    assert callback is not None
    callback({"kind": "injection", "message": "nudge"})

    assert engine.counters == [("guard.injections", 1.0)]
    assert engine.metrics == []


def test_tool_block_event_maps_to_general_and_per_tool_counters(monkeypatch):
    engine = _FakeEngine()
    module = _fake_module(engine)
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: module
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    callback = make_telemetry_guard_callback()
    assert callback is not None
    callback({"kind": "tool_block", "tool": "remember", "message": "denied"})

    assert engine.counters == [
        ("guard.tool_block", 1.0),
        ("guard.tool_block.remember", 1.0),
    ]
    assert engine.metrics == []


# -- 静默降级 ------------------------------------------------------------------


def test_missing_skill_returns_none(monkeypatch):
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: None
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    assert make_telemetry_guard_callback() is None


def test_module_without_get_telemetry_returns_none(monkeypatch):
    import types

    mod = types.ModuleType("_fake_telemetry_no_getter")
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: mod
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    assert make_telemetry_guard_callback() is None


def test_telemetry_failure_silently_degrades(monkeypatch):
    """遥测调用抛异常时不冒泡，绝不影响 agent 主循环。"""
    engine = _FakeEngine()

    def boom(*args, **kwargs):
        raise RuntimeError("telemetry down")

    engine.increment_counter = boom
    module = _fake_module(engine)
    monkeypatch.setattr(
        "core.loop.guard_telemetry._load_telemetry_module", lambda: module
    )
    monkeypatch.setattr("core.loop.guard_telemetry._attempted", False)
    monkeypatch.setattr("core.loop.guard_telemetry._callbacks_cache", None)

    callback = make_telemetry_guard_callback()
    assert callback is not None
    callback({"kind": "blocked", "streak": 6, "message": "x"})  # 不应抛异常


# -- 环境变量开关 ---------------------------------------------------------------


def test_install_skips_without_env(monkeypatch):
    monkeypatch.delenv(ENV_GUARD_TELEMETRY, raising=False)

    class _Spec:
        guard_event_callback = None

    spec = _Spec()
    install_guard_telemetry(spec)
    assert spec.guard_event_callback is None


def test_install_skips_when_env_not_one(monkeypatch):
    monkeypatch.setenv(ENV_GUARD_TELEMETRY, "0")
    monkeypatch.setattr(
        "core.loop.guard_telemetry.make_telemetry_guard_callback",
        lambda: lambda e: None,
    )

    class _Spec:
        guard_event_callback = None

    spec = _Spec()
    install_guard_telemetry(spec)
    assert spec.guard_event_callback is None


def test_install_assembles_when_env_enabled(monkeypatch):
    monkeypatch.setenv(ENV_GUARD_TELEMETRY, "1")

    def fake_callback(event):
        return None

    monkeypatch.setattr(
        "core.loop.guard_telemetry.make_telemetry_guard_callback",
        lambda: fake_callback,
    )

    class _Spec:
        guard_event_callback = None

    spec = _Spec()
    install_guard_telemetry(spec)
    assert spec.guard_event_callback is fake_callback


def test_install_never_overrides_existing_callback(monkeypatch):
    monkeypatch.setenv(ENV_GUARD_TELEMETRY, "1")

    def existing(event):
        return None

    class _Spec:
        def __init__(self) -> None:
            # 实例属性（而非类属性）：避免 Python 描述符协议把函数包装成
            # bound method，导致 `is` 比较失败（每次访问都新建绑定对象）。
            self.guard_event_callback = existing

    spec = _Spec()
    install_guard_telemetry(spec)
    assert spec.guard_event_callback is existing
