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

from core.compat import get_runtime
from core.events import AgentSession, UserInput, serialize_event
from core.harness.policy import build_permission_engine
from core.harness.tools import default_coding_tools
from core.llm_runtime import get_workflow_provider

_SYSTEM_PROMPT = (
    "You are a coding agent working in a workspace directory. You have tools: "
    "read, write, edit, bash, grep, glob. Navigate with grep/glob, inspect "
    "with read, make targeted changes with edit (or write for new files), and "
    "run commands/tests with bash. After a write or edit, check the tool "
    "result for a 'Diagnostics detected' block and fix any reported errors. "
    "When the task is done, reply with a short summary."
)


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
    elif t == "agent_message":
        print(f"\n{msg.text}\n", flush=True)
    elif t == "error":
        print(f"! error: {msg.message}", file=sys.stderr, flush=True)
    elif t == "task_complete":
        print(f"· done ({msg.stop_reason})", flush=True)


async def _run(args: argparse.Namespace) -> int:
    workspace = os.path.abspath(args.workspace)
    os.makedirs(workspace, exist_ok=True)

    provider, profile = get_workflow_provider(phase="implementation", model=args.model)
    model = args.model or profile.model

    security_cfg = getattr(get_runtime().config, "security", None)
    engine = build_permission_engine(security_cfg, cwd=workspace)

    session = AgentSession(
        provider,
        default_coding_tools(workspace),
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        max_iterations=args.max_iterations,
        permission_checker=engine.evaluate,
    )

    if not args.json:
        print(
            f"deepcode exec · model={model} · workspace={workspace} · "
            f"permission={engine.mode.value}",
            file=sys.stderr,
            flush=True,
        )

    stop_reason = "completed"
    async for event in session.run_stream(UserInput(text=args.prompt)):
        if args.json:
            print(json.dumps(serialize_event(event), ensure_ascii=False), flush=True)
        else:
            _emit_human(event)
        if event.msg.type == "task_complete":
            stop_reason = event.msg.stop_reason

    return 0 if stop_reason == "completed" else 1


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
        "--max-iterations", type=int, default=40, help="Max agent turns (default 40)."
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
