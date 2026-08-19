"""P2-F1: SLM/LLM task-complexity routing (GenAI lesson 19)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.loop.slm_routing import (
    SUBTASK_COMPLEX,
    SUBTASK_MEDIUM,
    SUBTASK_SIMPLE,
    route_subtask,
    slm_routing_enabled,
)


def test_simple_class_routes_to_slm(monkeypatch):
    monkeypatch.setenv("DEEPCODE_SLM_MODEL", "phi-3-mini")
    decision = route_subtask(SUBTASK_SIMPLE, default_model="gpt-4o")
    assert decision.tier == "slm"
    assert decision.model == "phi-3-mini"
    assert decision.reason.startswith("simple")


def test_medium_class_routes_to_slm(monkeypatch):
    monkeypatch.setenv("DEEPCODE_SLM_MODEL", "mistral-7b")
    assert route_subtask(SUBTASK_MEDIUM).tier == "slm"


def test_complex_class_routes_to_llm(monkeypatch):
    monkeypatch.delenv("DEEPCODE_LLM_MODEL", raising=False)
    decision = route_subtask(SUBTASK_COMPLEX, default_model="deepseek-v4-pro")
    assert decision.tier == "llm"
    assert decision.model == "deepseek-v4-pro"


def test_unknown_class_defaults_to_llm():
    decision = route_subtask("bogus-class", default_model="m")
    assert decision.tier == "llm"
    assert "unknown" in decision.reason


def test_slm_without_configured_model_falls_back_to_llm(monkeypatch):
    monkeypatch.delenv("DEEPCODE_SLM_MODEL", raising=False)
    decision = route_subtask(SUBTASK_SIMPLE, default_model="m")
    assert decision.tier == "llm"
    assert "DEEPCODE_SLM_MODEL unset" in decision.reason


def test_routing_disabled_forces_llm(monkeypatch):
    monkeypatch.setenv("DEEPCODE_SLM_ROUTING", "0")
    monkeypatch.setenv("DEEPCODE_SLM_MODEL", "phi-3")
    assert slm_routing_enabled() is False
    decision = route_subtask(SUBTASK_SIMPLE, default_model="m")
    assert decision.tier == "llm"
    assert decision.override is True


def test_explicit_override_beats_env(monkeypatch):
    monkeypatch.setenv("DEEPCODE_SLM_MODEL", "env-slm")
    decision = route_subtask(SUBTASK_SIMPLE, slm_override="caller-slm")
    assert decision.model == "caller-slm"


def test_llm_override_applied(monkeypatch):
    monkeypatch.setenv("DEEPCODE_LLM_MODEL", "env-llm")
    decision = route_subtask(SUBTASK_COMPLEX, llm_override="caller-llm")
    assert decision.model == "caller-llm"
