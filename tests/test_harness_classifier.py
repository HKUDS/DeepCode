"""Tests for the P0-1 LLM risk classifier (Claude Code Auto-mode lesson)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.classifier import (
    LLMRiskClassifier,
    RiskLevel,
    classifier_enabled,
    classifier_model,
    parse_risk_verdict,
)

LOW = RiskLevel.LOW
MEDIUM = RiskLevel.MEDIUM
HIGH = RiskLevel.HIGH


class FakeProvider:
    """Minimal provider double matching the ``chat()`` base interface."""

    def __init__(self, content: str | None = None, error: Exception | None = None):
        self._content = content
        self._error = error
        self.calls: list[dict] = []

    async def chat(self, messages, model=None, max_tokens=0, temperature=0.0, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self._error is not None:
            raise self._error
        from core.providers.base import LLMResponse

        return LLMResponse(content=self._content)


# ---- verdict parsing ---------------------------------------------------------


def test_parse_plain_json():
    v = parse_risk_verdict('{"risk": "low", "reason": "edits workspace file"}')
    assert v is not None and v.level is LOW and "workspace" in v.reason


def test_parse_markdown_fenced_json():
    v = parse_risk_verdict('```json\n{"risk": "high", "reason": "rm -rf /"}\n```')
    assert v is not None and v.level is HIGH


def test_parse_stray_prose_around_json():
    v = parse_risk_verdict(
        'Here you go: {"risk": "medium", "reason": "installs a dep"}'
    )
    assert v is not None and v.level is MEDIUM


def test_parse_single_quoted_keys():
    v = parse_risk_verdict("{'risk': 'low', 'reason': 'fine'}")
    assert v is not None and v.level is LOW


def test_parse_rejects_garbage():
    assert parse_risk_verdict("I don't know") is None
    assert parse_risk_verdict(None) is None
    assert parse_risk_verdict('{"risk": "nuclear", "reason": "x"}') is None
    assert parse_risk_verdict("[1,2,3]") is None


# ---- classifier behavior ----------------------------------------------------


@pytest.mark.asyncio
async def test_classify_low_auto_allows():
    provider = FakeProvider('{"risk": "low", "reason": "in-workspace edit"}')
    clf = LLMRiskClassifier(provider)
    verdict = await clf.classify("edit", {"file_path": "a.py"}, "needs confirmation")
    assert verdict.decisive and verdict.level is LOW
    # Prompt shape: system + user with the tool call details.
    assert provider.calls[0]["messages"][0]["role"] == "system"
    assert "edit" in provider.calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_classify_high_falls_through():
    provider = FakeProvider('{"risk": "high", "reason": "touches credentials"}')
    clf = LLMRiskClassifier(provider)
    verdict = await clf.classify("bash", {"command": "cat ~/.ssh/id_rsa"}, "ask")
    assert verdict.decisive and verdict.level is HIGH


@pytest.mark.asyncio
async def test_classify_provider_error_is_fail_open_medium():
    provider = FakeProvider(error=RuntimeError("provider down"))
    clf = LLMRiskClassifier(provider, timeout_s=1.0)
    verdict = await clf.classify("bash", {"command": "git push"}, "ask")
    # Fail-open: not decisive, falls back to the human path, never auto-allows.
    assert not verdict.decisive
    assert verdict.error is not None
    assert verdict.level is MEDIUM


@pytest.mark.asyncio
async def test_classify_unparseable_reply_is_fail_open():
    provider = FakeProvider("sorry, cannot classify")
    clf = LLMRiskClassifier(provider)
    verdict = await clf.classify("write", {"file_path": "x"}, "ask")
    assert not verdict.decisive
    assert "unparseable" in (verdict.error or "")


@pytest.mark.asyncio
async def test_classify_model_and_max_tokens_forwarded():
    provider = FakeProvider('{"risk": "low", "reason": "ok"}')
    clf = LLMRiskClassifier(provider, model="deepseek-v4-flash", max_tokens=64)
    await clf.classify("read", {"file_path": "a"}, "ask")
    call = provider.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["max_tokens"] == 64
    assert call["temperature"] == 0.0


@pytest.mark.asyncio
async def test_classify_temperature_is_zero_for_stability():
    provider = FakeProvider('{"risk": "low", "reason": "ok"}')
    clf = LLMRiskClassifier(provider)
    await clf.classify("read", {"file_path": "a"}, "ask")
    assert provider.calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_classify_arguments_truncated_for_huge_payloads():
    provider = FakeProvider('{"risk": "medium", "reason": "big"}')
    clf = LLMRiskClassifier(provider)
    huge = {"file_path": "x", "content": "A" * 5000}
    await clf.classify("write", huge, "ask")
    user_msg = provider.calls[0]["messages"][1]["content"]
    assert len(user_msg) < 2600  # 2000-char args cap + template overhead


# ---- env switches -----------------------------------------------------------


def test_classifier_env_switches(monkeypatch):
    monkeypatch.delenv("DEEPCODE_RISK_CLASSIFIER", raising=False)
    assert classifier_enabled() is False
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER", value)
        assert classifier_enabled() is True
    for value in ("0", "false", "off", "banana"):
        monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER", value)
        assert classifier_enabled() is False


def test_classifier_model_env(monkeypatch):
    monkeypatch.delenv("DEEPCODE_RISK_CLASSIFIER_MODEL", raising=False)
    assert classifier_model() is None
    monkeypatch.setenv("DEEPCODE_RISK_CLASSIFIER_MODEL", "deepseek-v4-flash")
    assert classifier_model() == "deepseek-v4-flash"
