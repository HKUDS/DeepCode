"""Tests for the keepalive gate + interval scheduler (deterministic)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.schedule.keepalive import Continuation  # noqa: E402
from core.schedule.scheduler import RunOutcome, run_scheduled  # noqa: E402


# -- gate --------------------------------------------------------------------


def test_gate_stops_when_goal_reached():
    g = Continuation(max_runs=10)
    d = g.should_continue(runs_done=1, clean_exit=True, goal_reached=True)
    assert not d.go and "goal" in d.reason


def test_gate_stops_on_unclean_exit():
    g = Continuation(max_runs=10)
    d = g.should_continue(runs_done=1, clean_exit=False, goal_reached=False)
    assert not d.go and "clean" in d.reason


def test_gate_stops_at_cap():
    g = Continuation(max_runs=3)
    d = g.should_continue(runs_done=3, clean_exit=True, goal_reached=False)
    assert not d.go and "cap" in d.reason


def test_gate_continues_otherwise():
    g = Continuation(max_runs=10)
    assert g.should_continue(runs_done=1, clean_exit=True, goal_reached=False).go


# -- scheduler ---------------------------------------------------------------


async def _nosleep(_):  # injected sleep — no real wall-clock
    return None


def test_scheduler_runs_until_goal():
    # Goal reached on the 3rd run.
    async def task(i):
        return RunOutcome(goal_reached=(i == 2))

    outcomes = asyncio.run(
        run_scheduled(task, interval=0, gate=Continuation(max_runs=10), sleep=_nosleep)
    )
    assert len(outcomes) == 3
    assert outcomes[-1].goal_reached


def test_scheduler_respects_cap():
    async def task(i):
        return RunOutcome(goal_reached=False)  # never done

    outcomes = asyncio.run(
        run_scheduled(task, interval=0, gate=Continuation(max_runs=4), sleep=_nosleep)
    )
    assert len(outcomes) == 4  # stopped at the run cap, not forever


def test_scheduler_stops_on_crash():
    async def task(i):
        raise RuntimeError("boom")

    outcomes = asyncio.run(
        run_scheduled(task, interval=0, gate=Continuation(max_runs=5), sleep=_nosleep)
    )
    assert len(outcomes) == 1  # crash = unclean exit → gate stops
    assert not outcomes[0].clean_exit


def test_scheduler_sleeps_between_runs_and_hooks():
    slept: list[float] = []
    seen: list[int] = []

    async def sleeper(secs):
        slept.append(secs)

    async def task(i):
        return RunOutcome(goal_reached=(i == 1))

    asyncio.run(
        run_scheduled(
            task,
            interval=5.0,
            gate=Continuation(max_runs=10),
            sleep=sleeper,
            on_run=lambda n, o: seen.append(n),
        )
    )
    assert seen == [1, 2]  # on_run fired per run
    assert slept == [5.0]  # slept once, between run 1 and run 2 (not after the last)
