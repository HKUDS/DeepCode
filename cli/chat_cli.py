"""``deepcode chat`` — interactive multi-turn agent over the event stream.

The interactive sibling of ``deepcode exec``: one persistent
:class:`~core.events.session.AgentSession` drives many turns from a REPL,
rendering the SQ/EQ event stream live (tool calls as they run, the assistant's
reply at the end). Because the session persists, conversation history — and
therefore the P2 context-compaction ladder — carries across turns, so a long
chat stays within the model's window instead of overflowing.

Reads a line per turn from stdin (so it is equally usable interactively or fed
a script). Meta-commands: ``/exit`` (or EOF) quits, ``/reset`` starts a fresh
session, ``/history`` prints the turn count.

    python -m cli.chat_cli --workspace ./proj
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.agent_setup import build_agent_session
from core.events import UserInput


def _render(event) -> None:
    msg = event.msg
    t = msg.type
    if t == "tool_started":
        print(f"  → {msg.name}", flush=True)
    elif t == "tool_completed":
        print(f"  {'✗' if msg.is_error else '✓'} {msg.name}", flush=True)
    elif t == "agent_message":
        print(f"\n{msg.text}\n", flush=True)
    elif t == "error":
        print(f"! error: {msg.message}", file=sys.stderr, flush=True)


async def _turn(session, text: str) -> None:
    async for event in session.run_stream(UserInput(text=text)):
        _render(event)


def _read_line(prompt: str) -> str | None:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


async def _repl(args: argparse.Namespace) -> int:
    session, model, engine = build_agent_session(
        workspace=args.workspace,
        model=args.model,
        max_iterations=args.max_iterations,
    )
    workspace = os.path.abspath(args.workspace)
    print(
        f"deepcode chat · model={model} · workspace={workspace} · "
        f"permission={engine.mode.value}",
        file=sys.stderr,
        flush=True,
    )
    print("Type your task. /exit to quit, /reset to clear, /history for turn count.")

    turns = 0
    while True:
        line = _read_line("\n› ")
        if line is None or line.strip() == "/exit":
            print("bye", flush=True)
            return 0
        text = line.strip()
        if not text:
            continue
        if text == "/reset":
            session, model, engine = build_agent_session(
                workspace=args.workspace,
                model=args.model,
                max_iterations=args.max_iterations,
            )
            turns = 0
            print("(session reset)", flush=True)
            continue
        if text == "/history":
            print(f"({turns} turn(s) in this session)", flush=True)
            continue
        await _turn(session, text)
        turns += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode chat",
        description="Interactive multi-turn coding agent on the DeepCode kernel.",
    )
    parser.add_argument("--workspace", "-w", default=os.getcwd())
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--max-iterations", type=int, default=40)
    args = parser.parse_args(argv)
    return asyncio.run(_repl(args))


if __name__ == "__main__":
    raise SystemExit(main())
