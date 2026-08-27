"""Model-visible means logged — the standing form of the dsh session-log rule.

``core/agent_runtime/runner.py`` states the rule and names the failure it
prevents: a message that reaches a model request but never reaches canonical
persistence makes a resumed Session "silently rebuild a DIFFERENT history than
the model actually saw".

This test is that sentence, executable. It records every ``messages`` list the
provider is handed across a tool-bearing conversation, then rebuilds the
history from ``session.jsonl`` alone and asserts the durable part of each
request survives the round trip. Per-request transients (the system prompt and
the environment slot, both re-derived from the live process) are excluded by
construction, not by exception.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.tui.app as tui_app
from core import agent_setup
from core.agent_runtime.context import EnvironmentContext
from core.providers.base import LLMResponse, ToolCallRequest
from core.sessions import SessionStore
from core.sessions.transcript import visible_kernel_history


class _Profile:
    model = "fake-model"


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(
        agent_setup,
        "get_workflow_provider",
        lambda **_kwargs: (provider, _Profile()),
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


class _RecordingProvider:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.requests.append([dict(m) for m in kwargs.get("messages", [])])
        index = min(self.calls, len(self.replies) - 1)
        self.calls += 1
        reply = self.replies[index]
        if isinstance(reply, LLMResponse):
            return reply
        return LLMResponse(content=reply, finish_reason="stop")


def _durable(messages: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The part of a request the canonical file is responsible for."""
    kept: list[tuple[Any, ...]] = []
    for message in messages:
        if message.get("role") == "system":
            continue
        if EnvironmentContext.is_history_message(message):
            continue
        kept.append(
            (
                message.get("role"),
                message.get("content"),
                tuple(
                    str(call.get("id"))
                    for call in message.get("tool_calls") or ()
                    if isinstance(call, dict)
                ),
                str(message.get("tool_call_id") or ""),
            )
        )
    return kept


def test_every_model_visible_message_is_reconstructable(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = _RecordingProvider(
        [
            LLMResponse(
                content="Reading it now.",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="bash",
                        arguments={"command": "echo ALPHA"},
                    )
                ],
            ),
            "first answer",
            LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="call-2",
                        name="bash",
                        arguments={"command": "echo BETA"},
                    )
                ],
            ),
            "second answer",
        ]
    )
    _patch_provider(monkeypatch, provider)
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import core.sessions.store as store_module

    monkeypatch.setattr(store_module, "_DEFAULT_STORE", None)
    monkeypatch.setattr("sys.stdin", io.StringIO("run alpha\nrun beta\n/exit\n"))

    assert (
        tui_app.main(
            ["--workspace", str(workspace), "--trust", "--access", "full-access"]
        )
        == 0
    )

    store = SessionStore(tmp_path / "sessions")
    session_id = store.list_sessions()[0].session_id
    stored = store.get_session(session_id)
    assert stored is not None
    rebuilt = _durable(visible_kernel_history(stored.messages))

    assert provider.requests, "the run made no provider request"
    for index, request in enumerate(provider.requests, start=1):
        durable = _durable(request)
        assert durable == rebuilt[: len(durable)], (
            f"request {index} contains model-visible messages the canonical "
            f"record cannot rebuild.\n  saw:      {durable}\n"
            f"  rebuilt:  {rebuilt[: len(durable)]}"
        )

    # The tool call and its result are the two categories the rule was added
    # for: they reach the model and used to reach nothing else.
    assert any(role == "tool" for role, *_ in rebuilt)
    assert any(calls for _role, _content, calls, _tid in rebuilt)


def test_a_message_the_record_drops_fails_the_invariant(monkeypatch, tmp_path):
    """The guard has to be able to fail — otherwise it guards nothing."""
    from core.sessions.models import SessionMessage

    request = [
        {"role": "user", "content": "run alpha"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "content": "ALPHA", "tool_call_id": "call-1"},
    ]
    lossy = [SessionMessage(role="user", content="run alpha")]
    rebuilt = _durable(visible_kernel_history(lossy))
    durable = _durable(request)
    assert durable != rebuilt[: len(durable)]
