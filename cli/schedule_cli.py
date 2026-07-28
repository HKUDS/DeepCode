"""``deepcode schedule`` — run a loop or memory consolidation on a keepalive
schedule (P3, L4).

Self-wakes on an interval and keeps going only while the keepalive gate is
open (clean exit ∧ not done ∧ under the run cap). Two jobs:

    # tidy this workspace's memory now, then every hour, up to 5 times
    python -m cli.schedule_cli autodream -w ./proj --every 3600 --max-runs 5

    # run a durable Goal with an explicit evidence command
    python -m cli.schedule_cli loop "fix the failing tests" \\
        -w ./proj -t "python -m pytest -q" --every 60 --max-runs 10

``--once`` runs a single pass and exits (the default when ``--every`` is 0).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from cli.goal_runner import GoalRunOptions, run_goal
from cli.execution_options import add_reasoning_effort_argument
from core.domain.thread_goal import ThreadGoalStatus
from core.loop.autodream import consolidate_memory
from core.schedule.keepalive import Continuation
from core.schedule.scheduler import RunOutcome, run_scheduled

_console = Console()


def _autodream_task(
    workspace: str,
    model: str | None,
    connection_id: str | None,
    reasoning_effort: str | None,
):
    async def task(run_index: int) -> RunOutcome:
        result = await consolidate_memory(
            workspace,
            model=model,
            connection_id=connection_id,
            reasoning_effort=reasoning_effort,
        )
        detail = (
            f"{result.notes_before}->{result.notes_after} notes"
            if result.ran
            else "no memory"
        )
        # autodream has no terminal "goal" — it is maintenance; a clean run
        # that changed nothing is the natural stopping point.
        done = not result.ran or result.notes_after == result.notes_before
        return RunOutcome(goal_reached=done, detail=detail)

    return task


def _loop_task(args) -> "callable":
    async def task(run_index: int) -> RunOutcome:
        workspace = os.path.abspath(args.workspace)
        result = await run_goal(
            GoalRunOptions(
                objective=args.goal,
                workspace=workspace,
                completion_evidence_command=args.test_cmd,
                model=args.model,
                connection_id=args.connection,
                reasoning_effort=args.reasoning_effort,
                token_budget=args.token_budget,
                max_iterations=args.max_iterations,
            )
        )
        return RunOutcome(
            goal_reached=result.goal.status is ThreadGoalStatus.COMPLETE,
            detail=result.goal.status.value,
        )

    return task


def _run(args: argparse.Namespace) -> int:
    workspace = os.path.abspath(args.workspace)
    os.makedirs(workspace, exist_ok=True)
    interval = 0.0 if args.once else float(args.every)
    max_runs = 1 if args.once else args.max_runs

    if args.job == "autodream":
        task = _autodream_task(
            workspace,
            args.model,
            args.connection,
            args.reasoning_effort,
        )
    else:
        task = _loop_task(args)

    _console.print(
        f"[bold cyan]✳ DeepCode schedule[/] [grey58]job[/] {args.job} "
        f"[grey58]every[/] {interval:g}s [grey58]max runs[/] {max_runs}"
    )

    def on_run(n: int, outcome: RunOutcome) -> None:
        mark = "green" if outcome.goal_reached else "grey58"
        _console.print(
            f"  [{mark}]run {n}[/] — {outcome.detail} "
            f"[grey58]({'clean' if outcome.clean_exit else 'crashed'})[/]"
        )

    outcomes = asyncio.run(
        run_scheduled(
            task,
            interval=interval,
            gate=Continuation(max_runs=max_runs),
            on_run=on_run,
        )
    )
    last = outcomes[-1] if outcomes else None
    ok = bool(last and last.goal_reached and last.clean_exit)
    _console.print(
        f"[{'bold green' if ok else 'bold yellow'}]· schedule done[/] "
        f"({len(outcomes)} run(s))"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode schedule",
        description="Run a loop or memory consolidation on a keepalive schedule.",
    )
    parser.add_argument("job", choices=("autodream", "loop"))
    parser.add_argument("goal", nargs="?", default="", help="Goal (loop job only).")
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument("--test-cmd", "-t", default="")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--connection", "-c", default=None)
    add_reasoning_effort_argument(parser)
    parser.add_argument(
        "--every", type=float, default=0, help="Interval seconds (0 = once)."
    )
    parser.add_argument("--max-runs", type=int, default=5)
    parser.add_argument("--token-budget", type=int, default=None)
    parser.add_argument("--max-iterations", type=int, default=40)
    parser.add_argument("--once", action="store_true", help="Run a single pass.")
    args = parser.parse_args(argv)
    if args.job == "loop" and not args.goal:
        parser.error("the 'loop' job requires a goal argument")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
