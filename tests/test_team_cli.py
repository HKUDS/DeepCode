"""Tests for the ``deepcode team`` CLI rendering (P4.f).

The CLI is a thin streaming wrapper around the Coordinator, so the thing worth
testing is that every lifecycle phase renders without crashing and shows the
right signal. We drive the real event renderer with synthetic TeamEvents and
capture a rich Console into a buffer — no LLM, no subprocess.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.team_cli import _make_on_event  # noqa: E402
from core.loop.backpressure import TestResult as _TestResult  # noqa: E402
from core.team.board import Task  # noqa: E402
from core.team.coordinator import TeamEvent  # noqa: E402
from core.team.worker import WorkerResult  # noqa: E402
from core.team.worktree import MergeResult  # noqa: E402


def _capture(events):
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    on_event = _make_on_event(console)
    for e in events:
        on_event(e)
    return buf.getvalue()


def test_renders_full_successful_lifecycle():
    tasks = [
        Task("a", title="build add", deps=[]),
        Task("b", title="build sub", deps=["a"]),
    ]
    out = _capture(
        [
            TeamEvent("decomposed", tasks=tasks),
            TeamEvent("task_started", task=tasks[0]),
            TeamEvent(
                "task_done",
                task=tasks[0],
                result=WorkerResult("w0", "a", True, "succeeded", 2),
                merge=MergeResult("a", clean=True, detail="merged"),
            ),
            TeamEvent("gate", gate=_TestResult(True, True, 0, "3 passed", "", "sig")),
        ]
    )
    assert "decomposed into 2 task(s)" in out
    assert "build add" in out and "after a" in out  # dep shown
    assert "built & merged" in out
    assert "gate" in out and "3 passed" in out


def test_renders_conflict_and_failure_paths():
    task = Task("x", title="edit shared")
    out = _capture(
        [
            TeamEvent(
                "task_done",
                task=task,
                result=WorkerResult("w0", "x", True, "succeeded", 1),
                merge=MergeResult(
                    "x", clean=False, conflicts=["shared.py"], detail="conflict"
                ),
            ),
            TeamEvent(
                "task_done",
                task=Task("y"),
                result=WorkerResult("w1", "y", False, "exhausted", 5),
                merge=MergeResult("y", clean=False, detail="worker failed"),
            ),
            TeamEvent("gate", gate=_TestResult(True, False, 1, "1 failed", "", "sig")),
        ]
    )
    assert "merge conflict" in out and "shared.py" in out
    assert "failed" in out and "exhausted" in out
    assert "gate" in out and "1 failed" in out


def test_gate_none_is_handled():
    out = _capture([TeamEvent("gate", gate=None)])
    assert "no overall test command" in out
