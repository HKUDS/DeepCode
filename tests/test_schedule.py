"""Tests for the keepalive gate + interval scheduler (deterministic).

Also covers the two control-plane pieces added 2026-09-19
(``should_run`` pre-turn gate, ``turn_transaction`` writeback-before-spend
ordering) — see docs/2026-09-19_调研-LOOPX与DEEPCODE适配评估-report.md.
"""

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
from core.schedule.should_run import SpendGate  # noqa: E402
from core.schedule.turn_transaction import (  # noqa: E402
    PHASES,
    ValidationResult,
    run_turn,
)


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


# -- pre-turn spend gate (P0) -------------------------------------------------
#
# LoopX's `quota should-run` semantics, reduced to what a DEEPCODE loop can
# actually know: the answer is a *typed envelope*, and every path that is not
# provably healthy / exhausted / stagnant is a refusal.


def test_spend_gate_allows_a_healthy_run():
    d = SpendGate(max_runs=10).should_run(
        runs_done=0, clean_exit=True, goal_reached=False
    )
    assert d.should_run and d.decision == "run" and d.state == "ready"


def test_spend_gate_stops_when_goal_reached():
    d = SpendGate(max_runs=10).should_run(
        runs_done=2, clean_exit=True, goal_reached=True
    )
    assert not d.should_run and d.state == "complete" and d.decision == "skip"


def test_spend_gate_stops_on_unclean_exit():
    d = SpendGate(max_runs=10).should_run(
        runs_done=1, clean_exit=False, goal_reached=False
    )
    assert not d.should_run and d.state == "blocked_health"


def test_spend_gate_stops_at_run_cap():
    d = SpendGate(max_runs=3).should_run(
        runs_done=3, clean_exit=True, goal_reached=False
    )
    assert not d.should_run and d.state == "exhausted"


def test_spend_gate_stops_on_stagnation():
    d = SpendGate(max_runs=10, max_stagnant_runs=3).should_run(
        runs_done=3, clean_exit=True, goal_reached=False, stagnant_runs=3
    )
    assert not d.should_run and d.state == "blocked_no_progress"
    # A stalled loop needs an owner decision, not another automatic turn.
    assert d.waiting_on == "owner"
    assert d.recommended_action  # never an empty envelope


def test_spend_gate_stagnation_below_threshold_still_runs():
    d = SpendGate(max_runs=10, max_stagnant_runs=3).should_run(
        runs_done=2, clean_exit=True, goal_reached=False, stagnant_runs=2
    )
    assert d.should_run


def test_spend_gate_progress_brake_is_off_at_zero():
    """max_stagnant_runs=0 disables the brake (the CLI default: opt-in)."""
    d = SpendGate(max_runs=10, max_stagnant_runs=0).should_run(
        runs_done=5, clean_exit=True, goal_reached=False, stagnant_runs=99
    )
    assert d.should_run


def test_spend_gate_rejects_negative_counters():
    d = SpendGate().should_run(runs_done=-1, clean_exit=True, goal_reached=False)
    assert not d.should_run and d.state == "blocked_validation"


def test_spend_gate_rejects_contradictory_input():
    """A crash cannot have reached the goal — refuse rather than guess."""
    d = SpendGate().should_run(runs_done=1, clean_exit=False, goal_reached=True)
    assert not d.should_run and d.state == "blocked_validation"


def test_spend_gate_never_defaults_to_run():
    """Fail-closed sweep: no bad input yields permission to spend."""
    g = SpendGate(max_runs=5, max_stagnant_runs=2)
    bad = [
        {"runs_done": 0, "clean_exit": False, "goal_reached": False},
        {"runs_done": 0, "clean_exit": False, "goal_reached": True},
        {"runs_done": 99, "clean_exit": True, "goal_reached": False},
        {"runs_done": 3, "clean_exit": True, "goal_reached": False, "stagnant_runs": 2},
        {"runs_done": 1, "clean_exit": True, "goal_reached": True},
        {"runs_done": -1, "clean_exit": True, "goal_reached": False},
        {
            "runs_done": 1,
            "clean_exit": True,
            "goal_reached": False,
            "stagnant_runs": -1,
        },
    ]
    for kwargs in bad:
        d = g.should_run(**kwargs)
        assert not d.should_run, kwargs
        assert d.decision == "skip" and d.reason and d.recommended_action, kwargs
        assert set(d.as_dict()) == {
            "decision",
            "should_run",
            "state",
            "reason",
            "recommended_action",
            "waiting_on",
            "runs_done",
        }


# -- turn transaction (P1): writeback before spend ----------------------------


def test_turn_transaction_spends_only_after_writeback():
    order: list[str] = []

    async def execute():
        order.append("execute")
        return {"change": 1}

    async def validate(result):
        order.append("validate")
        return ValidationResult(True)

    async def writeback(result):
        order.append("writeback")

    async def spend(result):
        order.append("spend")

    receipt = asyncio.run(
        run_turn(execute=execute, validate=validate, writeback=writeback, spend=spend)
    )
    assert order == ["execute", "validate", "writeback", "spend"]
    assert receipt.phases == PHASES
    assert receipt.spent and receipt.written_back and receipt.outcome == "spent"


def test_turn_transaction_does_not_spend_when_writeback_fails():
    spent: list[bool] = []

    async def execute():
        return 1

    async def writeback(result):
        raise OSError("disk full")

    async def spend(result):  # pragma: no cover - must never run
        spent.append(True)

    receipt = asyncio.run(run_turn(execute=execute, writeback=writeback, spend=spend))
    assert spent == []  # the invariant under test
    assert not receipt.spent and not receipt.written_back
    assert receipt.outcome == "writeback_failed"
    assert receipt.phases == (
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
    )


def test_turn_transaction_does_not_write_back_or_spend_on_invalid_result():
    touched: list[str] = []

    async def execute():
        return "untyped"

    async def validate(result):
        return ValidationResult(False, "result was not a typed change record")

    async def writeback(result):  # pragma: no cover - must never run
        touched.append("writeback")

    async def spend(result):  # pragma: no cover - must never run
        touched.append("spend")

    receipt = asyncio.run(
        run_turn(execute=execute, validate=validate, writeback=writeback, spend=spend)
    )
    assert touched == []
    assert receipt.outcome == "invalid_result"
    assert receipt.phases == ("host_execute", "typed_result", "validation")


def test_turn_transaction_records_execute_failure():
    async def execute():
        raise RuntimeError("host crashed")

    async def spend(result):  # pragma: no cover - must never run
        raise AssertionError("spend ran after a failed execute")

    async def writeback(result):  # pragma: no cover - must never run
        raise AssertionError("writeback ran after a failed execute")

    receipt = asyncio.run(run_turn(execute=execute, writeback=writeback, spend=spend))
    assert receipt.outcome == "execute_failed"
    assert receipt.phases == ("host_execute",)
    assert not receipt.executed and not receipt.written_back and not receipt.spent


def test_turn_transaction_keeps_writeback_when_spend_fails():
    written: list[str] = []

    async def execute():
        return "ok"

    async def writeback(result):
        written.append(result)

    async def spend(result):
        raise TimeoutError("quota service down")

    receipt = asyncio.run(run_turn(execute=execute, writeback=writeback, spend=spend))
    assert written == ["ok"]  # durable work is not rolled back by a spend failure
    assert receipt.written_back and not receipt.spent
    assert receipt.outcome == "spend_failed"


def test_turn_transaction_refuses_spend_without_writeback():
    """Wiring guard: spending with nowhere to write back is a contract error."""

    async def execute():
        return 1

    async def spend(result):  # pragma: no cover - guard fires first
        raise AssertionError("unreachable")

    with pytest.raises(ValueError, match="writeback"):
        asyncio.run(run_turn(execute=execute, spend=spend))


def test_turn_transaction_without_spend_hook_stops_after_writeback():
    async def execute():
        return 1

    async def writeback(result):
        return None

    receipt = asyncio.run(run_turn(execute=execute, writeback=writeback))
    assert receipt.written_back and not receipt.spent
    assert receipt.outcome == "spend_skipped"
    assert receipt.phases == (
        "host_execute",
        "typed_result",
        "validation",
        "durable_writeback",
    )


# -- scheduler wiring: the gate and the ordering are enforced in the loop ------


def test_scheduler_stops_on_stagnation():
    """The loop that used to spin to max_runs now stops on no progress."""

    async def task(i):
        return RunOutcome(goal_reached=False, progress_made=False)

    outcomes = asyncio.run(
        run_scheduled(
            task,
            interval=0,
            gate=Continuation(max_runs=10),
            sleep=_nosleep,
            spend_gate=SpendGate(max_runs=10, max_stagnant_runs=2),
        )
    )
    assert len(outcomes) == 2  # stopped by the progress brake, not by the cap


def test_scheduler_resets_stagnation_when_progress_resumes():
    async def task(i):
        # progress on run 2 resets the counter, so the brake never trips
        return RunOutcome(goal_reached=False, progress_made=(i == 1))

    outcomes = asyncio.run(
        run_scheduled(
            task,
            interval=0,
            gate=Continuation(max_runs=4),
            sleep=_nosleep,
            spend_gate=SpendGate(max_runs=4, max_stagnant_runs=2),
        )
    )
    assert len(outcomes) == 4  # cap still bounds it; brake did not fire


def test_scheduler_reports_denied_decision():
    seen: list[object] = []

    async def task(i):
        return RunOutcome(goal_reached=False, progress_made=False)

    asyncio.run(
        run_scheduled(
            task,
            interval=0,
            gate=Continuation(max_runs=10),
            sleep=_nosleep,
            spend_gate=SpendGate(max_runs=10, max_stagnant_runs=1),
            on_decision=seen.append,
        )
    )
    assert [d.should_run for d in seen] == [True, False]
    assert seen[-1].state == "blocked_no_progress"


def test_scheduler_spends_only_after_writeback():
    order: list[str] = []

    async def task(i):
        return RunOutcome(goal_reached=(i == 1))

    async def writeback(n, outcome):
        order.append("writeback")

    async def spend(n, outcome):
        order.append("spend")

    asyncio.run(
        run_scheduled(
            task,
            interval=0,
            gate=Continuation(max_runs=10),
            sleep=_nosleep,
            writeback=writeback,
            spend=spend,
        )
    )
    assert order == ["writeback", "spend", "writeback", "spend"]


def test_scheduler_refuses_spend_without_writeback_hook():
    async def task(i):
        return RunOutcome(goal_reached=True)

    async def spend(n, outcome):  # pragma: no cover - guard fires first
        raise AssertionError("unreachable")

    with pytest.raises(ValueError, match="writeback"):
        asyncio.run(
            run_scheduled(
                task,
                interval=0,
                gate=Continuation(max_runs=1),
                sleep=_nosleep,
                spend=spend,
            )
        )


def test_scheduler_does_not_spend_when_writeback_fails():
    spent: list[bool] = []

    async def task(i):
        return RunOutcome(goal_reached=True)

    async def writeback(n, outcome):
        raise OSError("disk full")

    async def spend(n, outcome):  # pragma: no cover - must never run
        spent.append(True)

    asyncio.run(
        run_scheduled(
            task,
            interval=0,
            gate=Continuation(max_runs=1),
            sleep=_nosleep,
            writeback=writeback,
            spend=spend,
        )
    )
    assert spent == []


# -- `deepcode schedule` -----------------------------------------------------


@pytest.fixture
def scheduled(monkeypatch):
    """Record how the CLI asks the scheduler to run, without running a job."""
    from cli import schedule_cli

    calls: list[dict] = []

    async def fake_run_scheduled(
        task, *, interval, gate, on_run=None, spend_gate=None, on_decision=None
    ):
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
