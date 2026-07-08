"""``deepcode loop`` — run a goal to completion autonomously (P3, L4).

The user-facing entry to loop engineering: give it a goal and a test command,
and it drives an autonomous, test-checked :class:`~core.loop.task.LoopTask` —
one thing per round, real test backpressure, shadow-git checkpoints — until
the tests are green or a circuit breaker fires.

    python -m cli.loop_cli "build a CLI calculator with add/sub and tests" \\
        --workspace ./calc --test-cmd "python -m pytest -q" --max-rounds 6

Exit code is 0 when the goal is reached (tests green), 1 otherwise, so it
drops into CI and scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel

from core.loop.state import STATUS_SUCCEEDED
from core.loop.task import LoopTask

_STATUS_STYLE = {
    "succeeded": "bold green",
    "exhausted": "bold yellow",
    "stalled": "bold yellow",
    "error": "bold red",
}


def _run(args: argparse.Namespace) -> int:
    console = Console()
    workspace = os.path.abspath(args.workspace)
    os.makedirs(workspace, exist_ok=True)

    console.print(
        Panel.fit(
            "[bold cyan]✳ DeepCode loop[/]\n"
            f"[grey58]goal[/] {args.goal}\n"
            f"[grey58]test[/] {args.test_cmd or '(none)'}"
            f"  [grey58]workspace[/] {workspace}"
            f"  [grey58]max rounds[/] {args.max_rounds}",
            border_style="grey58",
        )
    )

    def on_event(state, record, test) -> None:
        console.print(
            f"[cyan]●[/] [bold]round {record.index}[/] "
            f"[grey58]({record.agent_stop_reason})[/]",
            highlight=False,
        )
        if not test.ran:
            console.print("  [grey58]⎿ tests not run (no test command)[/]")
        elif test.passed:
            console.print(f"  [green]⎿ ✓ {test.summary}[/]", highlight=False)
        else:
            console.print(f"  [red]⎿ ✗ {test.summary}[/]", highlight=False)

    task = LoopTask(
        goal=args.goal,
        workspace=workspace,
        test_command=args.test_cmd,
        model=args.model,
        max_rounds=args.max_rounds,
        max_iterations=args.max_iterations,
        on_event=on_event,
    )
    result = asyncio.run(task.run())
    state = result.state

    style = _STATUS_STYLE.get(state.status, "bold")
    console.print(
        f"\n[{style}]· loop {state.status}[/] — {state.stop_reason} "
        f"[grey58]({state.round_count} round(s))[/]"
    )
    console.print(
        f"[grey58]state → {os.path.join('.deepcode', 'loop', 'state.json')}[/]"
    )
    return 0 if state.status == STATUS_SUCCEEDED else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode loop",
        description="Autonomously drive a goal to passing tests, round by round.",
    )
    parser.add_argument("goal", help="What to build/fix (natural language).")
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument(
        "--test-cmd",
        "-t",
        default="",
        help="Command that verifies the goal (e.g. 'python -m pytest -q'). "
        "Without it the loop can't check progress and stops after one round.",
    )
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
