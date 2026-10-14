"""Oversized tool results spilled under the project's .deepcode/tool-results.

Every interactive session spills into the same "default" bucket, so the
retention sweep has to age out files inside it, while keeping whatever a
session's history still points at.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from core.agent_runtime import helpers
from core.agent_runtime.helpers import maybe_persist_tool_result
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.base import Tool, tool_parameters
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMResponse, ToolCallRequest

_OLD = time.time() - helpers._TOOL_RESULT_RETENTION_SECS - 24 * 60 * 60


def _spill(directory: Path, name: str, *, mtime: float | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("x" * 100, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_old_spills_in_the_shared_bucket_are_removed(tmp_path: Path) -> None:
    bucket = tmp_path / ".deepcode" / "tool-results" / "default"
    stale = _spill(bucket, "old.txt", mtime=_OLD)
    recent = _spill(bucket, "recent.txt")

    reference = maybe_persist_tool_result(tmp_path, None, "new", "y" * 50, max_chars=10)

    assert "Full output saved to:" in reference
    assert (bucket / "new.txt").exists()
    assert recent.exists()
    assert not stale.exists()


def test_the_sweep_runs_again_in_a_long_lived_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maybe_persist_tool_result(tmp_path, None, "first", "y" * 50, max_chars=10)
    stale = _spill(
        tmp_path / ".deepcode" / "tool-results" / "default", "old.txt", mtime=_OLD
    )

    maybe_persist_tool_result(tmp_path, None, "second", "y" * 50, max_chars=10)
    assert stale.exists()  # not on every spill: the sweep is rate-limited

    two_days_later = time.time() + 2 * 24 * 60 * 60
    monkeypatch.setattr(time, "time", lambda: two_days_later)
    maybe_persist_tool_result(tmp_path, None, "third", "y" * 50, max_chars=10)
    assert not stale.exists()


@tool_parameters({"type": "object", "properties": {}})
class _BigOutputTool(Tool):
    @property
    def name(self) -> str:
        return "big"

    @property
    def description(self) -> str:
        return "Return more output than fits inline."

    async def execute(self, **kwargs: Any) -> str:
        return "z" * 5000


class _ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
async def test_a_spill_the_history_still_references_is_kept(tmp_path: Path) -> None:
    bucket = tmp_path / ".deepcode" / "tool-results" / "default"
    earlier_reference = maybe_persist_tool_result(
        tmp_path, None, "earlier", "x" * 5000, max_chars=2000
    )
    referenced = bucket / "earlier.txt"
    os.utime(referenced, (_OLD, _OLD))
    forgotten = _spill(bucket, "forgotten.txt", mtime=_OLD)
    # That call swept the root already; let the run's own spill sweep it.
    helpers._SWEPT_TOOL_RESULT_ROOTS.clear()

    provider = _ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="now", name="big", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_BigOutputTool())
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "user", "content": "earlier task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "earlier",
                        "type": "function",
                        "function": {"name": "big", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "earlier",
                "name": "big",
                "content": earlier_reference,
            },
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "go on"},
        ],
        tools=registry,
        model="fake-model",
        max_iterations=4,
        max_tool_result_chars=2000,
        workspace=tmp_path,
    )

    result = await AgentRunner(provider).run(spec)

    assert result.final_content == "done"
    assert (bucket / "now.txt").exists()
    assert referenced.read_text(encoding="utf-8") == "x" * 5000
    assert not forgotten.exists()
