"""Offline test for `deepcode loop` — full CLI → LoopTask → real pytest.

The provider is scripted (no model): the agent "does nothing", the workspace
is pre-seeded so the real test command passes on round 0, and we assert the
CLI drives the loop to a succeeded exit. This exercises the default round
runner (build_agent_session) end-to-end without a network call.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli.loop_cli as loop_cli  # noqa: E402
import core.agent_setup as agent_setup  # noqa: E402
from core.providers.base import LLMResponse  # noqa: E402


class _Provider:
    def get_default_model(self):
        return "fake-model"

    async def chat_with_retry(self, **kwargs: Any):
        return LLMResponse(content="done", finish_reason="stop")


class _Profile:
    model = "fake-model"


def test_loop_cli_succeeds_on_green(monkeypatch, tmp_path, capsys):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_calc.py").write_text(
        textwrap.dedent(
            """
            from calc import add
            def test_add():
                assert add(2, 3) == 5
            """
        )
    )
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (_Provider(), _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )

    rc = loop_cli.main(
        [
            "keep calc.add working",
            "-w",
            str(ws),
            "-t",
            "python -m pytest -q",
            "--max-rounds",
            "3",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "round 0" in out
    assert "succeeded" in out
    # State file was written.
    from core.loop.state import LoopState

    assert LoopState.load(ws).status == "succeeded"


def test_loop_cli_fails_exit_when_tests_stay_red(monkeypatch, tmp_path, capsys):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # buggy
    (ws / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    monkeypatch.setattr(
        agent_setup, "get_workflow_provider", lambda **kw: (_Provider(), _Profile())
    )
    monkeypatch.setattr(
        agent_setup,
        "get_runtime",
        lambda: type("R", (), {"config": type("C", (), {"security": None})()})(),
    )
    # Agent does nothing, so tests never pass → non-zero exit.
    rc = loop_cli.main(
        ["fix it", "-w", str(ws), "-t", "python -m pytest -q", "--max-rounds", "2"]
    )
    assert rc == 1
