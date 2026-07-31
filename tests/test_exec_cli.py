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

from cli import exec_cli
from core import agent_setup
from core.providers.base import LLMResponse, ToolCallRequest
from core.sessions import SessionStore


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
    # exec builds its Agent session through the shared application factory.
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (provider, _Profile())
    )
    monkeypatch.delenv("DEEPCODE_PERMISSION_MODE", raising=False)
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
        [
            "--workspace",
            str(tmp_path),
            "--trust",
            "--access",
            "full-access",
            "--json",
            "create hello.py that prints hi",
        ]
    )
    assert rc == 0
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"

    # stdout is NDJSON events, terminating in task_complete
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    events = [json.loads(ln) for ln in lines]
    kinds = [e["msg"]["type"] for e in events]
    assert all(set(event) == {"id", "msg"} for event in events)
    assert kinds[0] == "turn_started"
    assert "tool_started" in kinds and "tool_completed" in kinds
    assert kinds[-1] == "task_complete"


def test_exec_error_exits_nonzero(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(
        [LLMResponse(content="boom", finish_reason="error", error_kind="test")]
    )
    _patch(monkeypatch, provider)
    rc = exec_cli.main(
        ["--workspace", str(tmp_path), "--trust", "--json", "do a thing"]
    )
    assert rc == 1


def test_exec_human_output(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(
        [LLMResponse(content="all done", finish_reason="stop")]
    )
    _patch(monkeypatch, provider)
    rc = exec_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "--trust",
            "--effort",
            "high",
            "say hello",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "all done" in out  # agent message rendered
    assert "effort=high" in captured.err


def test_exec_verbose_keeps_summary_and_provider_trace_separate(
    tmp_path,
    monkeypatch,
    capsys,
):
    provider = _ScriptedProvider(
        [
            LLMResponse(
                content="all done",
                reasoning_summary="Checked the constraints.",
                reasoning_content="Provider trace detail.",
                finish_reason="stop",
            )
        ]
    )
    _patch(monkeypatch, provider)

    rc = exec_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "--trust",
            "--transcript",
            "verbose",
            "say hello",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Checked the constraints." in output
    assert "provider reasoning details" in output
    assert "Provider trace detail." in output


def test_exec_summary_hides_reasoning_but_keeps_final_answer(
    tmp_path,
    monkeypatch,
    capsys,
):
    provider = _ScriptedProvider(
        [
            LLMResponse(
                content="all done",
                reasoning_content="Provider trace detail.",
                finish_reason="stop",
            )
        ]
    )
    _patch(monkeypatch, provider)

    rc = exec_cli.main(
        [
            "--workspace",
            str(tmp_path),
            "--trust",
            "--transcript",
            "summary",
            "say hello",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "Provider trace detail." not in output
    assert "all done" in output


def test_exec_refuses_untrusted_workspace_without_creating_a_session(
    tmp_path,
    monkeypatch,
    capsys,
):
    provider = _ScriptedProvider(
        [LLMResponse(content="must not run", finish_reason="stop")]
    )
    _patch(monkeypatch, provider)

    rc = exec_cli.main(["--workspace", str(tmp_path), "inspect this project"])

    assert rc == 1
    assert provider.calls == 0
    assert SessionStore().list_sessions() == []
    assert "--trust" in capsys.readouterr().err


def test_exec_resume_reuses_the_canonical_session_and_history(
    tmp_path,
    monkeypatch,
):
    provider = _ScriptedProvider(
        [
            LLMResponse(content="first answer", finish_reason="stop"),
            LLMResponse(content="second answer", finish_reason="stop"),
        ]
    )
    _patch(monkeypatch, provider)

    assert (
        exec_cli.main(
            ["--workspace", str(tmp_path), "--trust", "remember the first turn"]
        )
        == 0
    )
    store = SessionStore()
    summaries = store.list_sessions()
    assert len(summaries) == 1
    session_id = summaries[0].session_id

    assert exec_cli.main(["--resume", session_id, "continue the same task"]) == 0

    assert provider.calls == 2
    assert [summary.session_id for summary in store.list_sessions()] == [session_id]
    session = store.get_session(session_id)
    assert session is not None
    assert [message.role for message in session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all(
        message.metadata.get("client") == "headless" for message in session.messages
    )
