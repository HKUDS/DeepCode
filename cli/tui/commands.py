"""Slash command registry for the TUI — declarative, self-documenting.

Each command is a :class:`Command` row in ``REGISTRY``; ``/help`` renders
itself from the table, so adding a command is one entry + one handler and
nothing else (no if/elif ladder — the anti-hardcoding rule applied to UX).

Handlers receive the running :class:`~cli.tui.app.TuiApp` and the argument
string, and return an optional status line to print. They may mutate app
state (switch sessions, rebuild the agent) through the app's public methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

Handler = Callable[[Any, str], Awaitable[str | None]]


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    handler: Handler


async def _cmd_help(app, args: str) -> str | None:
    lines = ["", "commands:"]
    for cmd in REGISTRY.values():
        lines.append(f"  {cmd.usage:<18} {cmd.help}")
    lines.append("  @<path>            attach a file's content to your message")
    lines.append("")
    return "\n".join(lines)


async def _cmd_new(app, args: str) -> str | None:
    app.new_conversation(title=args.strip())
    return "started a new conversation"


async def _cmd_resume(app, args: str) -> str | None:
    target = args.strip()
    if not target:
        rows = app.bridge.list_recent(limit=15)
        if not rows:
            return "no stored sessions yet"
        lines = ["", "recent sessions (resume with /resume <id>):"]
        for s in rows:
            title = s.title or "(untitled)"
            lines.append(
                f"  {s.session_id}  {title[:44]:<44} {s.message_count:>3} msgs"
            )
        lines.append("")
        return "\n".join(lines)
    try:
        turns = app.resume_conversation(target)
    except ValueError as exc:
        return str(exc)
    return f"resumed {target} ({turns} messages restored)"


async def _cmd_model(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        return f"model: {app.model}"
    app.switch_model(wanted)
    return f"model switched to {app.model} (history preserved)"


async def _cmd_clear(app, args: str) -> str | None:
    app.clear_conversation()
    return "context cleared"


async def _cmd_exit(app, args: str) -> str | None:
    app.request_exit()
    return None


REGISTRY: dict[str, Command] = {
    c.name: c
    for c in (
        Command("help", "/help", "show this help", _cmd_help),
        Command("new", "/new [title]", "start a new conversation", _cmd_new),
        Command("resume", "/resume [id]", "list sessions / resume one", _cmd_resume),
        Command("model", "/model [id]", "show or switch the model", _cmd_model),
        Command("clear", "/clear", "clear the conversation context", _cmd_clear),
        Command("exit", "/exit", "quit (ctrl-d also works)", _cmd_exit),
    )
}


async def dispatch(app, line: str) -> str | None:
    """Route a ``/command args`` line; unknown commands get a hint."""
    body = line[1:].strip()
    name, _, args = body.partition(" ")
    cmd = REGISTRY.get(name.lower())
    if cmd is None:
        return f"unknown command: /{name} — try /help"
    return await cmd.handler(app, args)
