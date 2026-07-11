"""Tests for the P4 Coordinator — real git worktrees + real pytest gate.

The decomposer and worker backend are injected (no LLM): a fake worker writes
the file each task calls for. Everything else is real — worktree isolation,
3-way merge, the final CriteriaExecutedGate running actual pytest — so the
orchestration is verified against real execution, exactly the property the
team is built to guarantee.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.team.coordinator import Coordinator  # noqa: E402
from core.team.worker import WorkerResult  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")

_PYTEST = "python -m pytest -q"


class _FakeWorker:
    """A worker whose 'work' is a Python callback over its worktree."""

    def __init__(self, action):
        self.action = action

    async def run(self, task, workspace, *, worker_id):
        ok = self.action(task, Path(workspace))
        return WorkerResult(worker_id, task.id, bool(ok), "fake worker", 1)


def _decomposer(tasks):
    async def decompose(goal, num_workers, test_command):
        return tasks

    return decompose


def test_three_independent_tasks_merge_and_pass(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    # Three independent modules, each with its own test → clean parallel merges.
    tasks = [
        {"id": "a", "spec": "add", "test_command": _PYTEST, "deps": []},
        {"id": "b", "spec": "sub", "test_command": _PYTEST, "deps": []},
        {"id": "c", "spec": "mul", "test_command": _PYTEST, "deps": []},
    ]

    def action(task, wt: Path):
        op = {"a": ("add", "a + b"), "b": ("sub", "a - b"), "c": ("mul", "a * b")}
        fn, expr = op[task.id]
        (wt / f"{fn}.py").write_text(f"def {fn}(a, b):\n    return {expr}\n")
        (wt / f"test_{fn}.py").write_text(
            f"from {fn} import {fn}\ndef test_{fn}():\n    assert {fn}(6, 2) == "
            f"{eval(expr, {}, {'a': 6, 'b': 2})}\n"
        )
        return True

    coord = Coordinator(
        goal="build a calc lib",
        workspace=str(ws),
        test_command=_PYTEST,
        num_workers=3,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(action),
    )
    result = asyncio.run(coord.run())

    assert result.succeeded
    assert result.tasks_done == 3 and result.tasks_total == 3
    assert not result.conflicts
    # All three modules landed in the base and the whole suite passes.
    for fn in ("add", "sub", "mul"):
        assert (ws / f"{fn}.py").exists()
    assert result.gate.passed


def test_dependency_ordering_is_respected(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [
        {"id": "base", "spec": "base", "test_command": "", "deps": []},
        {"id": "dep", "spec": "dep", "test_command": "", "deps": ["base"]},
    ]
    seen: dict[str, bool] = {}

    def action(task, wt: Path):
        if task.id == "base":
            (wt / "base.txt").write_text("base\n")
        else:  # dep runs only after base merged → base.txt visible in its worktree
            seen["dep_saw_base"] = (wt / "base.txt").exists()
            (wt / "dep.txt").write_text("dep\n")
        return True

    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        num_workers=2,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(action),
    )
    result = asyncio.run(coord.run())
    assert result.succeeded
    assert seen.get("dep_saw_base") is True  # dependency ran & merged first


def test_conflicting_tasks_are_flagged_not_clobbered(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "shared.py").write_text("x = 0\n")
    tasks = [
        {"id": "a", "spec": "a", "test_command": "", "deps": []},
        {"id": "b", "spec": "b", "test_command": "", "deps": []},
    ]

    async def scenario():
        # A barrier makes both workers diverge from the SAME base before either
        # merges — deterministically exercising the concurrent-conflict path
        # (without it, whichever worker finishes first merges and the other
        # cleanly builds on top, which is also correct behaviour).
        barrier = asyncio.Barrier(2)

        class _ConcurrentWorker:
            async def run(self, task, workspace, *, worker_id):
                (Path(workspace) / "shared.py").write_text(
                    f"x = {1 if task.id == 'a' else 2}\n"
                )
                await barrier.wait()
                return WorkerResult(worker_id, task.id, True, "fake", 1)

        coord = Coordinator(
            goal="g",
            workspace=str(ws),
            num_workers=2,
            decomposer=_decomposer(tasks),
            worker_backend=_ConcurrentWorker(),
        )
        return await coord.run()

    result = asyncio.run(scenario())
    assert not result.succeeded  # the second overlapping merge conflicts
    assert "shared.py" in result.conflicts


def test_internal_state_is_not_committed_to_base(tmp_path):
    """The team commits the feature into the base repo, but NEVER its own
    scratch state (.deepcode board/loop) — the user's history stays clean."""
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [{"id": "a", "spec": "a", "test_command": "", "deps": []}]

    def action(task, wt: Path):
        (wt / "feature.py").write_text("VALUE = 1\n")
        # Simulate the worker's LoopTask writing internal state in its worktree.
        (wt / ".deepcode" / "loop").mkdir(parents=True, exist_ok=True)
        (wt / ".deepcode" / "loop" / "state.json").write_text("{}")
        return True

    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        num_workers=1,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(action),
    )
    result = asyncio.run(coord.run())
    assert result.succeeded

    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ws, capture_output=True, text=True
    ).stdout
    assert "feature.py" in tracked  # the real work landed in the base
    assert ".deepcode" not in tracked  # our bookkeeping did not


def test_failed_worker_fails_task_and_dependents(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [
        {"id": "a", "spec": "a", "test_command": "", "deps": []},
        {"id": "b", "spec": "b", "test_command": "", "deps": ["a"]},
    ]

    def action(task, wt: Path):
        return task.id != "a"  # task a's worker fails

    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        num_workers=2,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(action),
    )
    result = asyncio.run(coord.run())
    assert not result.succeeded
    # The loop still terminates: b is cascade-failed because a failed.
    assert result.tasks_done == 0


def test_events_are_emitted_in_lifecycle_order(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [
        {"id": "a", "spec": "a", "test_command": "", "deps": []},
        {"id": "b", "spec": "b", "test_command": "", "deps": ["a"]},
    ]

    def action(task, wt: Path):
        # A real passing test per task so the final pytest gate is green.
        (wt / f"test_{task.id}.py").write_text(
            f"def test_{task.id}():\n    assert True\n"
        )
        return True

    events: list = []
    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        test_command=_PYTEST,  # non-empty → a gate event fires
        num_workers=2,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(action),
        on_event=events.append,
    )
    result = asyncio.run(coord.run())
    assert result.succeeded

    phases = [e.phase for e in events]
    # Exactly one decompose up front and one gate at the end.
    assert phases[0] == "decomposed"
    assert phases[-1] == "gate"
    assert phases.count("decomposed") == 1 and phases.count("gate") == 1
    # The decompose event carries the whole plan.
    assert {t.id for t in events[0].tasks} == {"a", "b"}
    # Every task both started and finished.
    started = [e.task.id for e in events if e.phase == "task_started"]
    done = [e.task.id for e in events if e.phase == "task_done"]
    assert sorted(started) == ["a", "b"] and sorted(done) == ["a", "b"]
    # A task can only be reported done after it was reported started.
    for tid in ("a", "b"):
        assert phases.index("task_started") < phases.index("gate")
        s = next(
            i
            for i, e in enumerate(events)
            if e.phase == "task_started" and e.task.id == tid
        )
        d = next(
            i
            for i, e in enumerate(events)
            if e.phase == "task_done" and e.task.id == tid
        )
        assert s < d
    # The gate event carries the real pytest result.
    gate_event = events[-1]
    assert gate_event.gate is not None and gate_event.gate.ran


def test_raising_worker_does_not_crash_or_hang_the_team(tmp_path):
    """A worker that raises (not returns a failure) must fail only its own task
    and cascade to its dependents — the team still settles and returns."""
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [
        {"id": "boom", "spec": "boom", "test_command": "", "deps": []},
        {"id": "ok", "spec": "ok", "test_command": "", "deps": []},
        {"id": "dep", "spec": "dep", "test_command": "", "deps": ["boom"]},
    ]

    class _RaisingBackend:
        async def run(self, task, workspace, *, worker_id):
            if task.id == "boom":
                raise RuntimeError("kaboom")
            (Path(workspace) / f"{task.id}.txt").write_text(task.id)
            return WorkerResult(worker_id, task.id, True, "ok", 1)

    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        num_workers=2,
        decomposer=_decomposer(tasks),
        worker_backend=_RaisingBackend(),
    )
    result = asyncio.run(coord.run())  # must return, not raise or hang
    assert not result.succeeded
    # Only "ok" made it; "boom" errored and "dep" cascade-failed behind it.
    assert result.tasks_done == 1


def test_cycle_in_decomposition_raises(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tasks = [
        {"id": "a", "deps": ["b"]},
        {"id": "b", "deps": ["a"]},
    ]
    coord = Coordinator(
        goal="g",
        workspace=str(ws),
        num_workers=2,
        decomposer=_decomposer(tasks),
        worker_backend=_FakeWorker(lambda t, w: True),
    )
    from core.team.board import CycleError

    with pytest.raises(CycleError):
        asyncio.run(coord.run())
