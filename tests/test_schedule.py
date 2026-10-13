"""Tests for the keepalive gate + interval scheduler (deterministic)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

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


# -- `deepcode schedule` -----------------------------------------------------


@pytest.fixture
def scheduled(monkeypatch):
    """Record how the CLI asks the scheduler to run, without running a job."""
    from cli import schedule_cli

    calls: list[dict] = []

    async def fake_run_scheduled(task, *, interval, gate, on_run=None):
        calls.append({"interval": interval, "max_runs": gate.max_runs})
        return [RunOutcome(goal_reached=True)]

    monkeypatch.setattr(schedule_cli, "run_scheduled", fake_run_scheduled)
    return schedule_cli, calls


@pytest.mark.parametrize("every", ["0", "-5", "nan", "inf"])
def test_cli_rejects_an_interval_that_is_not_a_positive_number(
    scheduled, capsys, tmp_path, every
):
    schedule_cli, calls = scheduled
    with pytest.raises(SystemExit) as exit:
        schedule_cli.main(["loop", "goal", "-w", str(tmp_path), "--every", every])
    assert exit.value.code == 2
    assert "--every" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize("max_runs", ["0", "-1"])
def test_cli_rejects_a_run_cap_below_one(scheduled, capsys, tmp_path, max_runs):
    schedule_cli, calls = scheduled
    with pytest.raises(SystemExit) as exit:
        schedule_cli.main(
            ["loop", "goal", "-w", str(tmp_path), "--every", "60"]
            + ["--max-runs", max_runs]
        )
    assert exit.value.code == 2
    assert "--max-runs" in capsys.readouterr().err
    assert calls == []


def test_cli_without_an_interval_runs_once(scheduled, tmp_path):
    """The help says so; it used to run --max-runs (5) times back to back."""
    schedule_cli, calls = scheduled
    assert schedule_cli.main(["loop", "goal", "-w", str(tmp_path)]) == 0
    assert calls == [{"interval": 0.0, "max_runs": 1}]


def test_cli_with_an_interval_repeats_up_to_the_cap(scheduled, tmp_path):
    schedule_cli, calls = scheduled
    argv = ["loop", "goal", "-w", str(tmp_path), "--every", "60", "--max-runs", "3"]
    assert schedule_cli.main(argv) == 0
    assert calls == [{"interval": 60.0, "max_runs": 3}]
    assert schedule_cli.main([*argv, "--once"]) == 0
    assert calls[-1] == {"interval": 0.0, "max_runs": 1}
