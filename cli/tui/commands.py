"""Slash command registry for the TUI — declarative, self-documenting.

Each command is a :class:`Command` row in ``REGISTRY``; ``/help`` renders
itself from the table, so adding a command is one entry + one handler and
nothing else (no if/elif ladder — the anti-hardcoding rule applied to UX).

Handlers receive the running :class:`~cli.tui.app.TuiApp` and the argument
string, and return an optional status line to print. They may mutate app
state (switch sessions, rebuild the agent) through the app's public methods.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rich.cells import cell_len, set_cell_size

Handler = Callable[[Any, str], Awaitable[str | None]]


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    handler: Handler


_HELP_USAGE_COLUMN_CAP = 30


async def _cmd_help(app, args: str) -> str | None:
    """Aligned command table: the usage column fits the widest short usage,
    and an over-long usage moves its description to a wrapped second line
    instead of shattering the column for every row below it."""
    rows = [(cmd.usage, cmd.help) for cmd in REGISTRY.values()]
    rows.append(("@<path>", "attach a file's content to your message"))
    column = max(
        (len(usage) for usage, _ in rows if len(usage) <= _HELP_USAGE_COLUMN_CAP),
        default=_HELP_USAGE_COLUMN_CAP,
    )
    lines = ["", "commands:"]
    for usage, description in rows:
        if len(usage) <= column:
            lines.append(f"  {usage:<{column}}  {description}")
        else:
            lines.append(f"  {usage}")
            lines.append(f"  {'':<{column}}  {description}")
    lines.append("")
    return "\n".join(lines)


async def _cmd_new(app, args: str) -> str | None:
    try:
        app.new_conversation(title=args.strip())
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    return "started a new conversation"


_RESUME_ROWS = 15
_RESUME_TITLE_CELLS = 44
_PREFIX_SCAN_LIMIT = 500  # threads.list pagination ceiling


def _age(moment: datetime) -> str:
    """Compact relative age for listing rows ('now', '5m ago', '3d ago')."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _fit_cells(text: str, width: int) -> str:
    """Truncate to a terminal-cell budget with an ellipsis, CJK-safe."""
    if cell_len(text) <= width:
        return text
    return set_cell_size(text, width - 1).rstrip() + "…"


def _short_path(path: str) -> str:
    home = os.path.expanduser("~")
    return f"~{path[len(home) :]}" if path.startswith(home + os.sep) else path


def _resume_row(listing, *, show_origin: bool) -> str:
    title = _fit_cells(listing.title or "(untitled)", _RESUME_TITLE_CELLS)
    line = (
        f"  {listing.session_id:<8}  {_age(listing.updated_at):>7}  "
        f"{listing.message_count:>3} msgs  {title}"
    )
    if listing.is_current:
        line += "  · current"
    if show_origin:
        line += f"  [{_short_path(listing.workspace)}]"
    return line


def _resolve_session_prefix(app, target: str) -> str | list[str]:
    """Resolve an id prefix to a full session id.

    Returns the unique match, or the (possibly empty) list of ambiguous
    matches. The raw target is kept when nothing matches — the resume path
    reports unknown ids with the store's own error.
    """
    rows = app.thread_client.list_recent(
        limit=_PREFIX_SCAN_LIMIT,
        include_all=True,
    )
    matches = [row.session_id for row in rows if row.session_id.startswith(target)]
    if len(matches) == 1:
        return matches[0]
    return matches


async def _cmd_resume(app, args: str) -> str | None:
    target = args.strip()
    if not target or target.lower() == "all":
        # Default view is scoped to the current directory (the Claude Code /
        # Codex convention); `all` lifts the filter and shows origins.
        show_all = target.lower() == "all"
        rows = app.thread_client.list_recent(
            limit=_RESUME_ROWS,
            include_all=show_all,
        )
        if not rows:
            return (
                "no stored sessions yet"
                if show_all
                else "no sessions for this directory — try /resume all"
            )
        scope = "all sessions" if show_all else f"sessions in {app.workspace}"
        lines = ["", f"recent {scope} (resume with /resume <id or prefix>):"]
        lines.extend(_resume_row(row, show_origin=show_all) for row in rows)
        lines.append("")
        return "\n".join(lines)
    resolved = _resolve_session_prefix(app, target)
    if isinstance(resolved, list):
        if len(resolved) > 1:
            return f"ambiguous session id {target!r} — matches {', '.join(resolved)}"
        resolved = target  # no listed match; let the store answer for exact ids
    try:
        turns = app.resume_conversation(resolved)
    except (RuntimeError, ValueError) as exc:
        return str(exc)
    app.render_resume_tail()
    status = f"resumed {resolved} ({turns} messages restored)"
    origin = app.bridge.stored_workspace()
    if origin and origin != app.workspace:
        status += f"\nnote: this conversation was started in {origin}"
    return status


async def _cmd_model(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        return app.model_overview()
    connection, separator, model = wanted.partition(" ")
    if not separator:
        model = connection
        connection = ""
    try:
        await app.switch_model(
            model.strip(),
            connection_id=connection.strip() or None,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return f"model switch failed: {exc}"
    status = (
        f"model switched to {app.model} on connection "
        f"{app.thread_client.execution_profile.connection_id} · effort "
        f"{app.requested_reasoning_effort} (history preserved)"
    )
    if app.thread_client.has_active_turn():
        status += "\nnote: the active turn keeps its model — applies from the next turn"
    note = app.model_catalog_note()
    return f"{status}\n{note}" if note else status


async def _cmd_effort(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        profile = app.thread_client.execution_profile
        effective = profile.reasoning_effort or "provider default"
        return f"effort: {app.requested_reasoning_effort} · effective: {effective}"
    try:
        await app.switch_reasoning_effort(wanted)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"effort switch failed: {exc}"
    profile = app.thread_client.execution_profile
    effective = profile.reasoning_effort or "provider default"
    return (
        f"effort switched to {app.requested_reasoning_effort} "
        f"(effective: {effective}; history preserved)"
    )


async def _cmd_permissions(app, args: str) -> str | None:
    wanted = args.strip().casefold()
    if not wanted:
        return app.access_status()
    aliases = {
        "ask": "ask",
        "read-only": "read_only",
        "read_only": "read_only",
        "full-access": "full_access",
        "full_access": "full_access",
        "inherit": None,
        "default": None,
    }
    if wanted not in aliases:
        return "usage: /permissions [ask|read-only|full-access|inherit]"
    try:
        return await app.set_access_preset(aliases[wanted])
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Session access update failed: {exc}"


async def _cmd_preset(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        return app.agent_preset_overview()
    try:
        return app.set_agent_preset(None if wanted == "clear" else wanted)
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)


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


async def _cmd_compact(app, args: str) -> str | None:
    if args.strip():
        return "usage: /compact (no arguments)"
    try:
        return await app.compact_conversation()
    except (RuntimeError, ValueError) as exc:
        return str(exc)


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


async def _cmd_plugins(app, args: str) -> str | None:
    if args.strip():
        return "usage: /plugins"
    return app.list_plugins()


async def _cmd_mcp(app, args: str) -> str | None:
    return await app.manage_mcp(args)


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
            "permissions",
            "/permissions [preset]",
            "show or set this Session's tool access",
            _cmd_permissions,
        ),
        Command(
            "transcript",
            "/transcript [normal|verbose|summary]",
            "show or switch transcript detail (ctrl-o cycles)",
            _cmd_transcript,
        ),
        Command(
            "preset",
            "/preset [id|clear]",
            "list agent presets or select one for this blank Session",
            _cmd_preset,
        ),
        Command("skills", "/skills", "list discovered Skills", _cmd_skills),
        Command(
            "skill",
            "/skill <id|name>",
            "select a Skill for the next turn",
            _cmd_skill,
        ),
        Command("plugins", "/plugins", "list installed Plugins", _cmd_plugins),
        Command(
            "mcp",
            "/mcp [action]",
            "list, add, test, authorize, or manage MCP servers",
            _cmd_mcp,
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
        Command(
            "compact",
            "/compact",
            "summarize older turns to free context",
            _cmd_compact,
        ),
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
