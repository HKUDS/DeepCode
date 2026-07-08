"""Offline tests for the interactive TUI (piped mode, scripted provider).

The TUI's InputReader falls back to plain stdin when not a TTY, so the whole
REPL is drivable by monkeypatched stdin — no pty needed. The provider is
scripted (no network); session persistence goes to a tmp store via
DEEPCODE_SESSIONS_DIR.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.agent_setup as agent_setup  # noqa: E402
import cli.tui.app as tui_app  # noqa: E402
from cli.tui.input import expand_file_refs  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _ScriptedProvider:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0

    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        i = min(self.calls, len(self.replies) - 1)
        self.calls += 1
        return LLMResponse(content=self.replies[i], finish_reason="stop")


class _Profile:
    model = "fake-model"


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (provider, _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )


def _run_tui(
    monkeypatch, tmp_path, stdin_text: str, replies: list[str]
) -> tuple[int, Any]:
    provider = _ScriptedProvider(replies)
    _patch_provider(monkeypatch, provider)
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    # Fresh default store per test (the singleton caches the env root).
    import core.sessions.store as store_mod

    monkeypatch.setattr(store_mod, "_DEFAULT_STORE", None)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    rc = tui_app.main(["--workspace", str(tmp_path / "ws")])
    return rc, provider


def test_multi_turn_conversation(monkeypatch, tmp_path, capsys):
    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "first task\nsecond task\n/exit\n",
        ["reply one", "reply two"],
    )
    assert rc == 0
    assert provider.calls == 2
    out = capsys.readouterr().out
    assert "reply one" in out and "reply two" in out


def test_slash_help_lists_registry(monkeypatch, tmp_path, capsys):
    rc, _ = _run_tui(monkeypatch, tmp_path, "/help\n/exit\n", ["unused"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("/new", "/resume", "/model", "/clear", "/exit"):
        assert name in out


def test_unknown_command_hints(monkeypatch, tmp_path, capsys):
    rc, _ = _run_tui(monkeypatch, tmp_path, "/nope\n/exit\n", ["unused"])
    assert rc == 0
    assert "unknown command" in capsys.readouterr().out


def test_new_resets_history_and_model_switch_keeps_it(monkeypatch, tmp_path, capsys):
    rc, provider = _run_tui(
        monkeypatch,
        tmp_path,
        "hello\n/new\n/model other-model\n/exit\n",
        ["hi there"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "started a new conversation" in out
    assert "model switched to other-model" in out


def test_session_persisted_and_resumable(monkeypatch, tmp_path, capsys):
    # Conversation 1: one turn, then read the store to find the session id.
    rc, _ = _run_tui(
        monkeypatch, tmp_path, "remember the number 42\n/exit\n", ["noted: 42"]
    )
    assert rc == 0
    from core.sessions.store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    sid = sessions[0].session_id
    assert sessions[0].message_count == 2  # user + assistant
    # The session was titled from the first message.
    assert "remember the number" in sessions[0].title

    # Conversation 2: /resume restores the transcript into the live agent.
    rc2, provider2 = _run_tui(
        monkeypatch,
        tmp_path,
        f"/resume {sid}\nwhat number?\n/exit\n",
        ["you said 42"],
    )
    assert rc2 == 0
    out = capsys.readouterr().out
    assert f"resumed {sid}" in out


def test_resume_without_arg_lists_sessions(monkeypatch, tmp_path, capsys):
    _run_tui(monkeypatch, tmp_path, "task one\n/exit\n", ["done"])
    capsys.readouterr()
    rc, _ = _run_tui(monkeypatch, tmp_path, "/resume\n/exit\n", ["unused"])
    assert rc == 0
    assert "recent sessions" in capsys.readouterr().out


def test_expand_file_refs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("the secret is blue\n")
    expanded = expand_file_refs("summarize @notes.txt please", str(ws))
    assert "the secret is blue" in expanded
    assert "attached file: notes.txt" in expanded
    # Non-file tokens stay untouched, no attachment added.
    assert expand_file_refs("email @bob about it", str(ws)) == "email @bob about it"


def test_file_refs_fenced_to_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    out = expand_file_refs("read @../outside.txt", str(ws))
    assert "secret" not in out  # escape attempt is not attached
