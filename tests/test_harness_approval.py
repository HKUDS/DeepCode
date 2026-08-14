"""Tests for the P1.b2 terminal approver (ask → human decision)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.approval import TerminalApprover  # noqa: E402


def _approver(answers, *, interactive=True):
    it = iter(answers)
    out: list[str] = []
    return (
        TerminalApprover(
            input_fn=lambda _prompt: next(it),
            output_fn=out.append,
            is_interactive=lambda: interactive,
        ),
        out,
    )


def test_yes_allows_once():
    approver, _ = _approver(["y"])
    assert approver("write_file", {"file_path": "a.py"}, "mutating") is True


def test_no_denies():
    approver, _ = _approver(["n"])
    assert approver("execute_bash", {"command": "rm x"}, None) is False


def test_always_allows_and_remembers():
    # Only ONE answer provided; the second call must not prompt again.
    approver, _ = _approver(["always"])
    assert approver("write_file", {"file_path": "a.py"}) is True
    assert approver("write_file", {"file_path": "b.py"}) is True  # no prompt needed


def test_always_is_scoped_to_tool_name():
    approver, _ = _approver(["a", "n"])
    assert approver("write_file", {"file_path": "a.py"}) is True
    # A different tool still prompts (and here is denied).
    assert approver("execute_bash", {"command": "ls"}) is False


def test_non_interactive_denies_without_prompting():
    approver, out = _approver([], interactive=False)
    assert approver("write_file", {"file_path": "a.py"}) is False
    assert any("not interactive" in line for line in out)


def test_eof_denies():
    def raise_eof(_prompt):
        raise EOFError

    approver = TerminalApprover(
        input_fn=raise_eof, output_fn=lambda _s: None, is_interactive=lambda: True
    )
    assert approver("write_file", {"file_path": "a.py"}) is False


def test_summary_prefers_command_then_path():
    approver, out = _approver(["y"])
    approver("execute_bash", {"command": "pytest -q", "timeout": 30})
    assert any("command='pytest -q'" in line for line in out)


@pytest.mark.asyncio
async def test_as_async_bridges_to_sync():
    approver, _ = _approver(["y"])
    acall = approver.as_async()
    assert await acall("write_file", {"file_path": "a.py"}, "why") is True
