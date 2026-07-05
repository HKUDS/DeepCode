"""Offline tests for `deepcode chat` (interactive multi-turn entry).

Patches the provider (no model) and feeds scripted stdin lines to verify the
REPL runs multiple turns on one persistent session and honours meta-commands.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.agent_setup as agent_setup  # noqa: E402
import cli.chat_cli as chat_cli  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _ScriptedProvider:
    def __init__(self, reply: str = "done"):
        self.reply = reply
        self.calls = 0

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        self.calls += 1
        return LLMResponse(content=self.reply, finish_reason="stop")


class _Profile:
    model = "fake-model"


def _patch(monkeypatch, provider):
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (provider, _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def _feed(monkeypatch, lines):
    it = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr("builtins.input", fake_input)


def test_multi_turn_runs_on_one_session(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider(reply="handled")
    _patch(monkeypatch, provider)
    _feed(monkeypatch, ["first task", "second task", "/exit"])

    rc = chat_cli.main(["--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # Two turns → the scripted provider was hit at least twice, replies shown.
    assert provider.calls >= 2
    assert out.count("handled") >= 2
    assert "bye" in out


def test_history_and_reset_commands(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider()
    _patch(monkeypatch, provider)
    _feed(monkeypatch, ["do a thing", "/history", "/reset", "/history", "/exit"])

    rc = chat_cli.main(["--workspace", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 turn(s)" in out  # after the first task
    assert "(session reset)" in out
    assert "0 turn(s)" in out  # after reset


def test_eof_quits_cleanly(tmp_path, monkeypatch, capsys):
    provider = _ScriptedProvider()
    _patch(monkeypatch, provider)
    _feed(monkeypatch, [])  # immediate EOF

    rc = chat_cli.main(["--workspace", str(tmp_path)])
    assert rc == 0
