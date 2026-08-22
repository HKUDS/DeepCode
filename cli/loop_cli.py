"""Compatibility command for running a Thread Goal headlessly.

The Goal is executed by the same ordinary Turn runtime used by CLI and
Desktop. No Attempt/evaluator loop is created.

    python -m cli.loop_cli "build a CLI calculator with add/sub and tests" \\
        --workspace ./calc --test-cmd "python -m pytest -q" --token-budget 50000

    python -m cli.loop_cli --resume SESSION_ID

Exit code is 0 only when the working Agent marks the Goal complete from the
available evidence; all other terminal states return 1.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from cli.tui import theme

from cli.execution_options import (
    add_access_preset_argument,
    add_reasoning_effort_argument,
    add_workspace_trust_argument,
    parse_access_preset,
)
from cli.goal_runner import (
    GoalResumeOptions,
    GoalRunOptions,
    resume_goal,
    run_goal,
)
from cli.tui.renderer import EventRenderer
from core.application.errors import ApplicationError
from core.config import ConfigError
from core.domain.thread_goal import ThreadGoalStatus

_STATUS_STYLE = {
    "succeeded": "bold green",
    "exhausted": "bold yellow",
    "stalled": "bold yellow",
    "error": "bold red",
}


def _run(args: argparse.Namespace) -> int:
    console = Console()
    resuming = args.resume is not None
    workspace = (
        os.path.abspath(args.workspace or os.getcwd())
        if not resuming
        else os.path.abspath(args.workspace)
        if args.workspace
        else None
    )
    # Tool cards name paths relative to the workspace; a resumed run learns
    # its own from the stored Session, so it keeps absolute ones.
    renderer = EventRenderer(console, workspace=workspace)
    if not resuming:
        assert workspace is not None
        os.makedirs(workspace, exist_ok=True)
    goal_label = f"resume Session {args.resume}" if resuming else str(args.goal)

    console.print()
    console.print(f" {theme.brand_markup()} [bold]loop[/]")
    console.print(f" [{theme.META_STYLE}]goal[/] {goal_label}")
    console.print(
        f" [{theme.META_STYLE}]test {args.test_cmd or '(none)'} · "
        f"workspace {workspace or '(stored Session workspace)'} · "
        f"token budget {args.token_budget or 'none'}[/]"
    )
    console.print()

    def on_progress(goal) -> None:
        console.print(
            f"[cyan]●[/] [bold]Goal {goal.status.value}[/] "
            f"[grey58]({goal.tokens_used} tokens, "
            f"{goal.time_used_seconds}s)[/]",
            highlight=False,
        )

    try:
        if resuming:
            result = asyncio.run(
                resume_goal(
                    GoalResumeOptions(
                        session_id=args.resume,
                        workspace=workspace,
                        model=args.model,
                        connection_id=args.connection,
                        reasoning_effort=args.reasoning_effort,
                        token_budget=args.token_budget,
                        max_iterations=args.max_iterations,
                        trust_workspace=args.trust,
                        access_preset=parse_access_preset(args.access),
                    ),
                    on_progress=on_progress,
                    on_event=renderer.on_event,
                )
            )
        else:
            assert workspace is not None and args.goal is not None
            result = asyncio.run(
                run_goal(
                    GoalRunOptions(
                        objective=args.goal,
                        workspace=workspace,
                        completion_evidence_command=args.test_cmd,
                        model=args.model,
                        connection_id=args.connection,
                        reasoning_effort=args.reasoning_effort,
                        skill_identifiers=tuple(args.skill),
                        token_budget=args.token_budget,
                        max_iterations=args.max_iterations,
                        trust_workspace=args.trust,
                        access_preset=parse_access_preset(args.access),
                    ),
                    on_progress=on_progress,
                    on_event=renderer.on_event,
                )
            )
    except ApplicationError as exc:
        console.print(f"[bold red]· Goal failed to start[/] — {exc.user_message}")
        return 1
    except (ConfigError, OSError, ValueError) as exc:
        console.print(f"[bold red]· Goal failed to start[/] — {exc}")
        return 1
    status = result.goal.status.value
    presentation = (
        "succeeded"
        if result.goal.status is ThreadGoalStatus.COMPLETE
        else "exhausted"
        if result.goal.status is ThreadGoalStatus.BUDGET_LIMITED
        else "stalled"
        if result.goal.status is ThreadGoalStatus.BLOCKED
        else "error"
    )
    style = _STATUS_STYLE.get(presentation, "bold")
    console.print(
        f"\n[{style}]· Goal {status}[/] — Session {result.session_id} "
        f"[grey58]({result.goal.tokens_used} tokens · {result.workspace})[/]"
    )
    if result.outcome is not None:
        console.print(
            f"[grey58]reason[/] {result.outcome.reason}"
            + (
                f"\n[grey58]deciding Turn[/] {result.outcome.decided_by_turn_id}"
                if result.outcome.decided_by_turn_id
                else ""
            ),
            highlight=False,
        )
    return 0 if result.goal.status is ThreadGoalStatus.COMPLETE else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode loop",
        description="Run a durable Goal on the shared ordinary-Turn runtime.",
    )
    parser.add_argument(
        "goal",
        nargs="?",
        help="What to build/fix (natural language).",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume the existing Goal attached to a canonical Session.",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        default=None,
        help="Workspace for a new Goal, or an explicit process-local override "
        "when resuming. Resume otherwise uses the stored workspace.",
    )
    parser.add_argument(
        "--test-cmd",
        "-t",
        default="",
        help="For a new Goal, a command the Agent must run and inspect as evidence "
        "(for example: pytest or 'python -m pytest -q').",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model for a new Goal or the next resumed Turn.",
    )
    parser.add_argument(
        "--connection",
        "-c",
        default=None,
        help="Connection for a new Goal or the next resumed Turn.",
    )
    add_reasoning_effort_argument(parser)
    add_access_preset_argument(parser)
    add_workspace_trust_argument(parser)
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="ID_OR_NAME",
        help="For a new Goal, select a Skill for its Turns (repeatable).",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Total budget for a new Goal, or a larger budget when resuming.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional model-sampling limit for diagnostics (unlimited by default).",
    )
    args = parser.parse_args(argv)
    if (args.goal is None) == (args.resume is None):
        parser.error("provide exactly one of GOAL or --resume SESSION_ID")
    if args.resume is not None and args.test_cmd:
        parser.error("--test-cmd cannot rewrite the objective of an existing Goal")
    if args.resume is not None and args.skill:
        parser.error("--skill is only available when creating a new Goal")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
