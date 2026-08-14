"""Offline tests for autodream memory consolidation (mechanical parts).

The actual merging is an LLM behavior verified separately with a real model;
here we cover the no-op-when-empty guard and the before/after accounting with
a scripted (no-op) provider.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.agent_setup as agent_setup  # noqa: E402
from core.harness.memory import memory_dir  # noqa: E402
from core.loop.autodream import consolidate_memory  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _Provider:
    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        return LLMResponse(content="nothing to change", finish_reason="stop")


class _Profile:
    model = "fake-model"


def _patch(monkeypatch):
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (_Provider(), _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def test_noop_when_no_memory(monkeypatch, tmp_path):
    _patch(monkeypatch)
    result = asyncio.run(consolidate_memory(str(tmp_path)))
    assert result.ran is False
    assert result.notes_before == 0


def test_runs_and_counts_when_memory_exists(monkeypatch, tmp_path):
    _patch(monkeypatch)
    mem = memory_dir(tmp_path)
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- a\n- b")
    (mem / "note1.md").write_text("fact one")
    (mem / "note2.md").write_text("fact one (dup)")

    result = asyncio.run(consolidate_memory(str(tmp_path)))
    assert result.ran is True
    assert result.notes_before == 3  # scripted provider is a no-op → after == before
    assert result.notes_after == 3
