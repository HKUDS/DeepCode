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
    try:
        app.new_conversation(title=args.strip())
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    return "started a new conversation"


async def _cmd_resume(app, args: str) -> str | None:
    target = args.strip()
    if not target or target.lower() == "all":
        # Default view is scoped to the current directory (the Claude Code /
        # Codex convention); `all` lifts the filter and shows origins.
        show_all = target.lower() == "all"
        rows = app.bridge.list_recent(limit=15, include_all=show_all)
        if not rows:
            return (
                "no stored sessions yet"
                if show_all
                else "no sessions for this directory — try /resume all"
            )
        scope = "all sessions" if show_all else f"sessions in {app.workspace}"
        lines = ["", f"recent {scope} (resume with /resume <id>):"]
        for s in rows:
            title = s.title or "(untitled)"
            line = f"  {s.session_id}  {title[:40]:<40} {s.message_count:>3} msgs"
            if show_all:
                origin = app.bridge.workspace_of(s.session_id)
                line += f"  [{origin or 'no workspace recorded'}]"
            lines.append(line)
        lines.append("")
        return "\n".join(lines)
    try:
        turns = app.resume_conversation(target)
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    status = f"resumed {target} ({turns} messages restored)"
    origin = app.bridge.stored_workspace()
    if origin and origin != app.workspace:
        status += f"\nnote: this conversation was started in {origin}"
    return status


async def _cmd_model(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        profile = app.thread_client.execution_profile
        return (
            f"connection: {profile.connection_id} · model: {app.model} · "
            f"effort: {app.requested_reasoning_effort}"
        )
    connection, separator, model = wanted.partition(" ")
    if not separator:
        model = connection
        connection = ""
    try:
        await app.switch_model(
            model.strip(),
            connection_id=connection.strip() or None,
        )
    except (OSError, ValueError) as exc:
        return f"model switch failed: {exc}"
    return (
        f"model switched to {app.model} on connection "
        f"{app.thread_client.execution_profile.connection_id} · effort "
        f"{app.requested_reasoning_effort} (history preserved)"
    )


async def _cmd_effort(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        profile = app.thread_client.execution_profile
        effective = profile.reasoning_effort or "provider default"
        return f"effort: {app.requested_reasoning_effort} · effective: {effective}"
    try:
        await app.switch_reasoning_effort(wanted)
    except (OSError, ValueError) as exc:
        return f"effort switch failed: {exc}"
    profile = app.thread_client.execution_profile
    effective = profile.reasoning_effort or "provider default"
    return (
        f"effort switched to {app.requested_reasoning_effort} "
        f"(effective: {effective}; history preserved)"
    )


async def _cmd_transcript(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        return f"transcript mode: {app.renderer.transcript_mode.value}"
    try:
        return app.set_transcript_mode(wanted)
    except ValueError as exc:
        return str(exc)


async def _cmd_clear(app, args: str) -> str | None:
    try:
        app.clear_conversation()
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    return "context cleared"


async def _cmd_skills(app, args: str) -> str | None:
    return app.list_skills()


async def _cmd_skill(app, args: str) -> str | None:
    action, _, target = args.strip().partition(" ")
    if not action:
        return "usage: /skill <id|name> | remove <id|name> | clear"
    if action.lower() == "clear":
        return app.clear_skills()
    if action.lower() == "remove":
        if not target.strip():
            return "usage: /skill remove <id|name>"
        return app.remove_skill(target.strip())
    return app.select_skill(args.strip())


async def _cmd_goal(app, args: str) -> str | None:
    return await app.run_goal_command(args)


async def _cmd_queue(app, args: str) -> str | None:
    prompt = args.strip()
    if not prompt:
        return "usage: /queue <instruction>"
    return app.queue_turn(prompt)


async def _cmd_stop(app, args: str) -> str | None:
    return app.stop_turn()


async def _cmd_exit(app, args: str) -> str | None:
    app.request_exit()
    return None


REGISTRY: dict[str, Command] = {
    c.name: c
    for c in (
        Command("help", "/help", "show this help", _cmd_help),
        Command("new", "/new [title]", "start a new conversation", _cmd_new),
        Command(
            "resume",
            "/resume [id|all]",
            "list this directory's sessions / resume one",
            _cmd_resume,
        ),
        Command(
            "model",
            "/model [connection] [id]",
            "show or switch connection/model",
            _cmd_model,
        ),
        Command(
            "effort",
            "/effort [auto|off|level]",
            "show or switch reasoning effort",
            _cmd_effort,
        ),
        Command(
            "transcript",
            "/transcript [normal|verbose|summary]",
            "show or switch transcript detail (ctrl-o cycles)",
            _cmd_transcript,
        ),
        Command("skills", "/skills", "list discovered Skills", _cmd_skills),
        Command(
            "skill",
            "/skill <id|name>",
            "select a Skill for the next turn",
            _cmd_skill,
        ),
        Command(
            "goal",
            "/goal <text> | show | edit <text> | pause | resume | continue | wait | reopen [text] | clear",
            "run or manage this Session's durable, steerable Goal",
            _cmd_goal,
        ),
        Command(
            "queue",
            "/queue <instruction>",
            "send an instruction as the next durable Turn",
            _cmd_queue,
        ),
        Command("stop", "/stop", "interrupt the active Turn", _cmd_stop),
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
