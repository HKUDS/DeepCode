"""``deepcode exec`` — headless general coding agent (P2, L5).

The user-reachable entry for the P1+P2 kernel: it runs a general coding task
on an :class:`~core.events.session.AgentSession` with the native tool set
(read/write/edit/bash/grep/glob), the P1 permission engine + sandbox, and
streams the SQ/EQ event flow. This is the same driver a CI job, a team
worker, or the SWE-bench eval harness uses.

Usage:
    python -m cli.exec_cli "fix the failing test in mathlib.py"
    python -m cli.exec_cli --workspace ./proj --json "add a --verbose flag"

``--json`` emits one JSON event per line (NDJSON) to stdout; otherwise a
compact human-readable transcript. Exit code is 0 on a clean completion,
1 on an error/interrupt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent_setup import DEFAULT_MAX_ITERATIONS, build_agent_session
from cli.config_errors import format_config_error
from cli.execution_options import add_reasoning_effort_argument
from core.config import ConfigError
from core.events import UserInput, serialize_event
from core.skills.management import LocalSkillManager
from core.skills.models import MAX_SELECTED_SKILLS, SkillSelection


def _emit_human(event) -> None:
    msg = event.msg
    t = msg.type
    if t == "turn_started":
        print("· turn started", flush=True)
    elif t == "tool_started":
        print(f"  → {msg.name}", flush=True)
    elif t == "tool_completed":
        mark = "✗" if msg.is_error else "✓"
        print(f"  {mark} {msg.name}", flush=True)
    elif t == "skill_loaded":
        print(
            f"  ◇ skill {msg.invocation.name} ({msg.invocation.kind.value})",
            flush=True,
        )
    elif t == "skill_load_failed":
        print(f"! skill error: {msg.message}", file=sys.stderr, flush=True)
    elif t == "agent_message":
        print(f"\n{msg.text}\n", flush=True)
    elif t == "error":
        print(f"! error: {msg.message}", file=sys.stderr, flush=True)
    elif t == "task_complete":
        print(f"· done ({msg.stop_reason})", flush=True)


async def _run(args: argparse.Namespace) -> int:
    try:
        session, model, engine = build_agent_session(
            workspace=args.workspace,
            model=args.model,
            connection_id=args.connection,
            reasoning_effort=args.reasoning_effort,
            max_iterations=args.max_iterations,
        )
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr, flush=True)
        return 1
    workspace = os.path.abspath(args.workspace)
    try:
        try:
            manager = LocalSkillManager(workspace)
            selected: list[SkillSelection] = []
            for identifier in args.skill:
                record = manager.select(identifier)
                selected.append(SkillSelection(skill_id=record.id, name=record.name))
            selections = tuple(selected)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr, flush=True)
            return 1

        if not args.json:
            effort = (
                args.reasoning_effort
                or session.execution_profile.reasoning_effort
                or "auto"
            )
            print(
                f"deepcode exec · model={model} · workspace={workspace} · "
                f"effort={effort} · permission={engine.mode.value}",
                file=sys.stderr,
                flush=True,
            )

        stop_reason = "completed"
        async for event in session.run_stream(
            UserInput(text=args.prompt, skills=selections)
        ):
            if args.json:
                print(
                    json.dumps(serialize_event(event), ensure_ascii=False),
                    flush=True,
                )
            else:
                _emit_human(event)
            if event.msg.type == "task_complete":
                stop_reason = event.msg.stop_reason

        return 0 if stop_reason == "completed" else 1
    finally:
        await session.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode exec",
        description="Run a general coding task headlessly on the DeepCode agent kernel.",
    )
    parser.add_argument("prompt", help="The coding task to perform.")
    parser.add_argument(
        "--workspace",
        "-w",
        default=os.getcwd(),
        help="Directory the agent works in (default: current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON event per line (NDJSON) instead of a transcript.",
    )
    parser.add_argument("--model", "-m", default=None, help="Override the model id.")
    parser.add_argument(
        "--connection",
        "-c",
        default=None,
        help="Use a named LLM connection from `deepcode provider list`.",
    )
    add_reasoning_effort_argument(parser)
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="ID_OR_NAME",
        help="Select a Skill for this turn (repeatable, maximum 8).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help=f"Max agent turns, a runaway backstop (default {DEFAULT_MAX_ITERATIONS}).",
    )
    args = parser.parse_args(argv)
    if len(args.skill) > MAX_SELECTED_SKILLS:
        parser.error(f"--skill may be specified at most {MAX_SELECTED_SKILLS} times")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
