"""Tests for P0-2 Phase-1 structured memory extraction (Codex lesson)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.memory_distill import (
    compose_deposit_text,
    extract_structured_memory,
    redact_secrets,
    structured_extraction_enabled,
)

# ---- redaction --------------------------------------------------------------


def test_redact_openai_style_key():
    out = redact_secrets("use sk-abc12345678901234567890 now")
    assert "sk-[REDACTED]" in out
    assert "abc12345678901234567890" not in out


def test_redact_aws_and_github():
    assert "AKIA[REDACTED]" in redact_secrets("key AKIAIOSFODNN7EXAMPLE here")
    assert "ghp_[REDACTED]" in redact_secrets(
        "token ghp_0123456789abcdefghijklmnopqrstuvwxyz"
    )


def test_redact_bearer_and_private_key():
    out = redact_secrets("Authorization: Bearer xyz123456789012345678901234567890")
    assert "[REDACTED]" in out and "xyz123456789012345678901234567890" not in out
    out2 = redact_secrets("-----BEGIN RSA PRIVATE KEY-----")
    assert "[REDACTED-PRIVATE-KEY]" in out2


def test_redact_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    out = redact_secrets(f"jwt {jwt}")
    assert "eyJhbGci" not in out
    assert "[JWT-REDACTED]" in out


def test_redact_keeps_normal_text():
    text = "The agent edited src/main.py and ran pytest — 42 passed."
    assert redact_secrets(text) == text


def test_redact_empty():
    assert redact_secrets("") == ""
    assert redact_secrets(None if False else "") == ""


# ---- compose ----------------------------------------------------------------


def test_compose_without_structured_is_redacted_transcript():
    transcript = "user asked about sk-abc12345678901234567890"
    out = compose_deposit_text(transcript, None)
    assert "sk-[REDACTED]" in out
    assert "abc12345678901234567890" not in out


def test_compose_with_structured_appends_sections():
    structured = {
        "raw_memory": "fact one\nfact two",
        "rollout_summary": "short summary",
        "rollout_slug": "fix-auth-flow",
    }
    out = compose_deposit_text("plain transcript", structured)
    assert "# structured summary" in out
    assert "short summary" in out
    assert "# raw memory" in out
    assert "fact one" in out


def test_compose_redacts_structured_fields():
    structured = {
        "raw_memory": "used key sk-abc12345678901234567890",
        "rollout_summary": "",
        "rollout_slug": "",
    }
    out = compose_deposit_text("", structured)
    assert "sk-[REDACTED]" in out
    assert "abc12345678901234567890" not in out


def test_compose_empty_input():
    assert compose_deposit_text("", None) == ""


# ---- structured extraction (provider double) --------------------------------


class FakeProvider:
    def __init__(self, content: str):
        self._content = content

    async def chat(self, messages, model=None, max_tokens=0, temperature=0.0, **kw):
        from core.providers.base import LLMResponse

        return LLMResponse(content=self._content)


def _run_extract(provider, transcript):
    import asyncio

    return asyncio.run(extract_structured_memory(transcript, provider, timeout_s=5))


def test_extract_valid_json():
    provider = FakeProvider(
        '{"raw_memory": "key sk-abc12345678901234567890 stored", '
        '"rollout_summary": "Fixed the auth flow", "rollout_slug": "fix-auth"}'
    )
    record = _run_extract(provider, "transcript here")
    assert record is not None
    assert record["rollout_summary"] == "Fixed the auth flow"
    # Secrets redacted from generated fields.
    assert "sk-[REDACTED]" in record["raw_memory"]
    assert "abc12345678901234567890" not in record["raw_memory"]


def test_extract_garbage_reply_returns_none():
    provider = FakeProvider("I cannot do that.")
    assert _run_extract(provider, "transcript") is None


def test_extract_provider_error_returns_none():
    class BoomProvider:
        async def chat(self, **kwargs):
            raise RuntimeError("down")

    assert _run_extract(BoomProvider(), "transcript") is None


def test_extract_empty_record_returns_none():
    provider = FakeProvider(
        '{"raw_memory": "", "rollout_summary": "", "rollout_slug": ""}'
    )
    assert _run_extract(provider, "transcript") is None


# ---- env switches -----------------------------------------------------------


def test_structured_extraction_env(monkeypatch):
    monkeypatch.delenv("DEEPCODE_MEMORY_DISTILL_STRUCTURED", raising=False)
    assert structured_extraction_enabled() is False
    for v in ("1", "true", "yes", "on"):
        monkeypatch.setenv("DEEPCODE_MEMORY_DISTILL_STRUCTURED", v)
        assert structured_extraction_enabled() is True
    for v in ("0", "false", "off", "banana"):
        monkeypatch.setenv("DEEPCODE_MEMORY_DISTILL_STRUCTURED", v)
        assert structured_extraction_enabled() is False
