"""``deepcode team`` — build a feature with a team of parallel workers (P4).

The user-facing entry to team intelligence: give it a feature and a test
command, and a lead decomposes it into a task board, N workers build the
sub-tasks concurrently in isolated git worktrees, each merge is a real 3-way
merge (conflicts are surfaced, never clobbered), and a final gate runs the
whole test command on the merged result.

    python -m cli.team_cli "add a REST API with users and auth, plus tests" \\
        --workspace ./app --test-cmd "python -m pytest -q" --workers 3

Exit code is 0 only when every task merged cleanly AND the overall tests are
green, so it drops into CI and scripts exactly like ``deepcode loop``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from core.team.coordinator import Coordinator, TeamEvent


def _render_plan(console: Console, event: TeamEvent) -> None:
    tasks = event.tasks or []
    tree = Tree(f"[bold cyan]decomposed into {len(tasks)} task(s)[/]")
    for t in tasks:
        deps = f"  [grey58]after {', '.join(t.deps)}[/]" if t.deps else ""
        label = t.title or t.spec[:60] or t.id
        tree.add(f"[bold]{t.id}[/] {label}{deps}")
    console.print(tree)


def _render_task_done(console: Console, event: TeamEvent) -> None:
    task, result, merge = event.task, event.result, event.merge
    tid = task.id if task else "?"
    if result is not None and result.succeeded and merge is not None and merge.clean:
        console.print(
            f"  [green]✓[/] [bold]{tid}[/] built & merged "
            f"[grey58]({result.rounds} round(s))[/]",
            highlight=False,
        )
    elif merge is not None and not merge.clean and merge.conflicts:
        console.print(
            f"  [red]✗[/] [bold]{tid}[/] merge conflict in "
            f"{', '.join(merge.conflicts)} [grey58](left unmerged)[/]",
            highlight=False,
        )
    else:
        why = (result.detail if result else "") or (merge.detail if merge else "")
        console.print(
            f"  [red]✗[/] [bold]{tid}[/] failed [grey58]({why})[/]",
            highlight=False,
        )


def _make_on_event(console: Console):
    def on_event(event: TeamEvent) -> None:
        if event.phase == "decomposed":
            _render_plan(console, event)
            console.print("[grey58]dispatching workers…[/]")
        elif event.phase == "task_started":
            tid = event.task.id if event.task else "?"
            console.print(f"  [cyan]▷[/] worker picked up [bold]{tid}[/]")
        elif event.phase == "task_done":
            _render_task_done(console, event)
        elif event.phase == "gate":
            gate = event.gate
            if gate is None:
                console.print("[grey58]· no overall test command — skipping gate[/]")
            elif gate.passed:
                console.print(f"[green]· gate ✓ {gate.summary}[/]", highlight=False)
            else:
                console.print(f"[red]· gate ✗ {gate.summary}[/]", highlight=False)

    return on_event


def _run(args: argparse.Namespace) -> int:
    console = Console()
    workspace = os.path.abspath(args.workspace)
    os.makedirs(workspace, exist_ok=True)

    console.print(
        Panel.fit(
            "[bold cyan]✳ DeepCode team[/]\n"
            f"[grey58]goal[/] {args.goal}\n"
            f"[grey58]test[/] {args.test_cmd or '(none)'}"
            f"  [grey58]workers[/] {args.workers}"
            f"  [grey58]workspace[/] {workspace}",
            border_style="grey58",
        )
    )

    coord = Coordinator(
        goal=args.goal,
        workspace=workspace,
        test_command=args.test_cmd,
        model=args.model,
        num_workers=args.workers,
        worker_max_rounds=args.worker_max_rounds,
        on_event=_make_on_event(console),
    )
    result = asyncio.run(coord.run())

    style = "bold green" if result.succeeded else "bold red"
    console.print(
        f"\n[{style}]· team {'succeeded' if result.succeeded else 'failed'}[/] — "
        f"{result.tasks_done}/{result.tasks_total} task(s) merged"
        + (f", conflicts in {', '.join(result.conflicts)}" if result.conflicts else "")
    )
    console.print(f"[grey58]{result.detail}[/]")
    return 0 if result.succeeded else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode team",
        description="Decompose a feature and build it with parallel workers.",
    )
    parser.add_argument("goal", help="The feature to build (natural language).")
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument(
        "--test-cmd",
        "-t",
        default="",
        help="Command that verifies the whole feature (e.g. 'python -m pytest -q'). "
        "Run as the final gate on the merged result; without it the team merges "
        "but cannot prove the feature works end to end.",
    )
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument(
        "--workers", type=int, default=3, help="How many workers build in parallel."
    )
    parser.add_argument(
        "--worker-max-rounds",
        type=int,
        default=5,
        help="Max test-driven rounds each worker spends on its sub-task.",
    )
    args = parser.parse_args(argv)
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
