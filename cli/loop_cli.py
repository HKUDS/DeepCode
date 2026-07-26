"""Compatibility command for running the shared durable Goal engine headlessly.

The user-facing entry to loop engineering: give it a goal and a test command,
and it creates a canonical Session Goal whose Attempts are ordinary Turns.
The old ``.deepcode/loop/state.json`` is retained as a read-only compatibility
projection; ``goal.jsonl`` beside the Session transcript is authoritative.

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

from cli.goal_runner import GoalRunOptions, run_goal
from cli.execution_options import add_reasoning_effort_argument
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.goal import GoalStatus
from core.loop.compat import project_goal_to_loop_state
from core.skills.management import LocalSkillManager

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

    def on_progress(record, evaluation) -> None:
        attempt = next(
            (
                item
                for item in record.attempts
                if evaluation is not None and item.id == evaluation.attempt_id
            ),
            record.latest_attempt,
        )
        if attempt is None:
            return
        console.print(
            f"[cyan]●[/] [bold]round {attempt.ordinal - 1}[/] "
            f"[grey58]({attempt.status.value})[/]",
            highlight=False,
        )
        if evaluation is None:
            console.print("  [grey58]⎿ tests not run (no test command)[/]")
        elif evaluation.verdict.value == "complete":
            console.print(
                f"  [green]⎿ ✓ {evaluation.reason}[/]",
                highlight=False,
            )
        else:
            console.print(
                f"  [red]⎿ ✗ {evaluation.reason}[/]",
                highlight=False,
            )

    try:
        manager = LocalSkillManager(workspace)
        skill_ids = tuple(manager.select(value).id for value in args.skill)
        result = asyncio.run(
            run_goal(
                GoalRunOptions(
                    objective=args.goal,
                    workspace=workspace,
                    verification=args.test_cmd,
                    model=args.model,
                    connection_id=args.connection,
                    reasoning_effort=args.reasoning_effort,
                    skill_ids=skill_ids,
                    max_attempts=args.max_rounds,
                    max_iterations=args.max_iterations,
                ),
                on_progress=on_progress,
            )
        )
    except (ApplicationError, ConfigError, OSError, ValueError) as exc:
        console.print(f"[bold red]· Goal failed to start[/] — {exc}")
        return 1
    state = project_goal_to_loop_state(
        result.record,
        workspace=workspace,
        test_command=args.test_cmd,
        max_rounds=args.max_rounds,
    )
    state.save()

    style = _STATUS_STYLE.get(state.status, "bold")
    console.print(
        f"\n[{style}]· loop {state.status}[/] — {state.stop_reason} "
        f"[grey58]({state.round_count} round(s))[/]"
    )
    console.print(
        f"[grey58]state → {os.path.join('.deepcode', 'loop', 'state.json')}[/]"
    )
    return 0 if result.record.goal.status is GoalStatus.COMPLETED else 1


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
        help="Discovered verification ID or matching command "
        "(for example: pytest or 'python -m pytest -q').",
    )
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--connection", "-c", default=None)
    add_reasoning_effort_argument(parser)
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="ID_OR_NAME",
        help="Select a Skill for every Goal Attempt (repeatable).",
    )
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
