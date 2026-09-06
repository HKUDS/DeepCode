"""Wiring tests for the headless ask → risk-classifier seam (P0-1).

``core.harness.classifier.build_risk_classifier`` returns the callable attached
to ``AgentRunSpec.risk_classifier``; the runner consults it only when an
``ask`` has no human approver (headless) and only auto-allows a decisive
``low`` verdict. These tests exercise the resolver (env gating + verdict
mapping) and assert the kernel seam is declared — no real LLM or keyring is
touched.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields

from core.agent_runtime.runner import AgentRunSpec
from core.harness.classifier import (
    RiskLevel,
    build_risk_classifier,
)
from core.providers.base import LLMResponse


class FakeProvider:
    def __init__(self, content: str):
        self._content = content

    async def chat(self, messages, model=None, max_tokens=0, temperature=0.0, **kwargs):
        return LLMResponse(content=self._content)


class BoomProvider(FakeProvider):
    async def chat(self, messages, model=None, max_tokens=0, temperature=0.0, **kwargs):
        raise RuntimeError("provider down")


def test_resolver_inert_unless_env_enabled(monkeypatch):
    monkeypatch.delenv("DEEPCODE_RISK_CLASSIFIER", raising=False)
    resolver = build_risk_classifier(
        FakeProvider('{"risk": "low", "reason": "ok"}')
    )
    assert asyncio.run(resolver("write", {"file_path": "x"}, "ask")) is None


def test_resolver_returns_decisive_low_when_enabled(monkeypatch):
    monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER", "1")
    resolver = build_risk_classifier(
        FakeProvider('{"risk": "low", "reason": "routine workspace edit"}')
    )
    verdict = asyncio.run(resolver("write", {"file_path": "x"}, "ask"))
    assert verdict is not None
    assert verdict.decisive
    assert verdict.level == RiskLevel.LOW


def test_resolver_medium_is_not_auto_allow(monkeypatch):
    monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER", "1")
    resolver = build_risk_classifier(
        FakeProvider('{"risk": "medium", "reason": "installs a package"}')
    )
    verdict = asyncio.run(resolver("bash", {"command": "pip install x"}, "ask"))
    assert verdict is not None
    assert verdict.level == RiskLevel.MEDIUM  # runner keeps deny for non-low


def test_resolver_error_is_not_decisive(monkeypatch):
    monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER", "1")
    resolver = build_risk_classifier(BoomProvider(""))
    verdict = asyncio.run(resolver("bash", {"command": "rm -rf /tmp/x"}, "ask"))
    assert verdict is not None
    assert not verdict.decisive  # fail-open-to-deny in headless


def test_agent_run_spec_declares_risk_classifier_seam():
    assert any(f.name == "risk_classifier" for f in fields(AgentRunSpec))
