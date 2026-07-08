"""Offline tests for `deepcode exec` (the headless agent entry).

Patches the provider so no network/model is needed; verifies the CLI drives
AgentSession + native tools, streams NDJSON events, actually writes a file,
and returns the right exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.agent_setup as agent_setup  # noqa: E402
import cli.exec_cli as exec_cli  # noqa: E402
from core.providers.base import LLMResponse, ToolCallRequest  # noqa: E402


class _ScriptedProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[i]


class _Profile:
    model = "fake-model"


def _patch(monkeypatch, provider):
    # exec now builds its session via core.agent_setup; patch there.
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (provider, _Profile())
    )
    # security config: none -> full_auto engine (still enforces denylist)
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def test_exec_writes_file_and_exits_zero(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="c1",
                        name="write",
                        arguments={"file_path": "hello.py", "content": "print('hi')\n"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Created hello.py", finish_reason="stop"),
        ]
    )
    _patch(monkeypatch, provider)

    rc = exec_cli.main(
        ["--workspace", str(tmp_path), "--json", "create hello.py that prints hi"]
    )
    assert rc == 0
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"

    # stdout is NDJSON events, terminating in task_complete
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]
    kinds = [e["msg"]["type"] for e in events]
    assert kinds[0] == "turn_started"
    assert "tool_started" in kinds and "tool_completed" in kinds
    assert kinds[-1] == "task_complete"


def test_exec_error_exits_nonzero(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(
        [LLMResponse(content="boom", finish_reason="error", error_kind="test")]
    )
    _patch(monkeypatch, provider)
    rc = exec_cli.main(["--workspace", str(tmp_path), "--json", "do a thing"])
    assert rc == 1


def test_exec_human_output(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(
        [LLMResponse(content="all done", finish_reason="stop")]
    )
    _patch(monkeypatch, provider)
    rc = exec_cli.main(["--workspace", str(tmp_path), "say hello"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "all done" in out  # agent message rendered
