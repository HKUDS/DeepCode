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

from cli.transcript import TranscriptMode
from cli.tui.picker import Picker, PickerItem, PickerScope, PickerVariant

Handler = Callable[[Any, str], Awaitable[str | None]]
# Candidate first arguments for tab completion: (app, typed prefix) -> values.
ArgumentProvider = Callable[[Any, str], "list[str]"]


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    help: str
    handler: Handler
    # Optional tab completion for the command's first argument — declared
    # per row so the input layer stays free of per-command knowledge.
    arguments: ArgumentProvider | None = None


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


def _session_ids(app, prefix: str) -> list[str]:
    """Stored session ids starting with ``prefix`` (any workspace)."""
    rows = app.thread_client.list_recent(
        limit=_PREFIX_SCAN_LIMIT,
        include_all=True,
    )
    return [row.session_id for row in rows if row.session_id.startswith(prefix)]


def _transcript_modes(app, prefix: str) -> list[str]:
    return [m.value for m in TranscriptMode if m.value.startswith(prefix)]


def _permission_presets(app, prefix: str) -> list[str]:
    return [name for name in _PERMISSION_CHOICES if name.startswith(prefix)]


def _effort_levels(app, prefix: str) -> list[str]:
    levels = ("auto", "none", *app.reasoning_options(app.model))
    return [level for level in levels if level.startswith(prefix)]


def _resolve_session_prefix(app, target: str) -> str | list[str]:
    """Resolve an id prefix to a full session id.

    Returns the unique match, or the (possibly empty) list of ambiguous
    matches. The raw target is kept when nothing matches — the resume path
    reports unknown ids with the store's own error.
    """
    matches = _session_ids(app, target)
    if len(matches) == 1:
        return matches[0]
    return matches


def _resume_target(app, resolved: str) -> str:
    """Resume one session id: the shared tail of every resume entry point."""
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


def _resume_picker_items(rows, *, show_origin: bool) -> list[PickerItem]:
    items = []
    for row in rows:
        detail = f"{_age(row.updated_at)} · {row.message_count} msgs · {row.session_id}"
        if show_origin:
            detail += f" · {_short_path(row.workspace)}"
        items.append(
            PickerItem(
                value=row.session_id,
                title=row.title or "(untitled)",
                detail=detail,
                disabled_reason="current session" if row.is_current else None,
            )
        )
    return items


async def _resume_via_picker(app, *, prefer_all: bool) -> str | None:
    """The dsh grammar: pick a session by TITLE, ids stay on the detail line."""
    local = app.thread_client.list_recent(
        limit=_PREFIX_SCAN_LIMIT,
        include_all=False,
    )
    every = app.thread_client.list_recent(
        limit=_PREFIX_SCAN_LIMIT,
        include_all=True,
    )
    if not every:
        return "no stored sessions yet"
    scopes = [
        PickerScope(
            f"this directory {_short_path(app.workspace)}",
            _resume_picker_items(local, show_origin=False),
        ),
        PickerScope(
            "all directories",
            _resume_picker_items(every, show_origin=True),
        ),
    ]
    initial = 1 if prefer_all or not local else 0
    chosen = await Picker(
        scopes,
        title="Resume session",
        initial_scope=initial,
    ).run()
    if chosen is None:
        return None
    return _resume_target(app, str(chosen.value))


async def _cmd_resume(app, args: str) -> str | None:
    target = args.strip()
    if not target or target.lower() == "all":
        # Default view is scoped to the current directory (the Claude Code /
        # Codex convention); `all` lifts the filter and shows origins.
        show_all = target.lower() == "all"
        if app.reader.interactive:
            return await _resume_via_picker(app, prefer_all=show_all)
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
    return _resume_target(app, resolved)


async def _switch_model_report(
    app,
    connection_id: str | None,
    model: str,
    *,
    reasoning_effort: str | None = None,
    change_effort: bool = False,
) -> str:
    """Switch and describe the outcome — shared by argument and picker paths."""
    kwargs = {"connection_id": connection_id}
    if change_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    try:
        await app.switch_model(model, **kwargs)
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


def _effort_variants(
    app,
    connection_id: str,
    model_id: str,
) -> tuple[tuple[PickerVariant, ...], int]:
    """The Shift+Tab ladder for one route: auto plus its published efforts.

    Adapter-owned values only (the dsh rule) — models without published
    reasoning controls get no ladder at all instead of invented levels.
    """
    efforts = app.reasoning_options(model_id, connection_id=connection_id)
    if not efforts:
        return (), 0
    ladder = ("auto", *efforts)
    variants = tuple(PickerVariant(value=level, label=level) for level in ladder)
    current = app.requested_reasoning_effort
    initial = ladder.index(current) if current in ladder else 0
    return variants, initial


def _model_picker_item(app, connection_id: str, model_id: str, detail: str):
    variants, initial = _effort_variants(app, connection_id, model_id)
    return PickerItem(
        value=(connection_id, model_id),
        title=f"{connection_id}/{model_id}",
        detail=detail,
        variants=variants,
        initial_variant=initial,
    )


async def _model_via_picker(app) -> str | None:
    """Pick a route from the configured catalogs (advisory, dsh's rule:
    the current route is shown even when no catalog lists it)."""
    profile = app.thread_client.execution_profile
    items: list[PickerItem] = []
    listed = False
    for view in app.connection_views():
        if not (view.get("configured") and view.get("enabled")):
            continue
        connection_id = str(view.get("id", ""))
        for model in view.get("manualModels") or []:
            model_id = str(model)
            current = (
                connection_id == profile.connection_id and model_id == profile.model_id
            )
            listed = listed or current
            items.append(
                _model_picker_item(
                    app,
                    connection_id,
                    model_id,
                    "current" if current else "",
                )
            )
    if not listed:
        items.insert(
            0,
            _model_picker_item(
                app,
                profile.connection_id,
                profile.model_id,
                "current · not in any catalog",
            ),
        )
    if len(items) < 2:
        # Nothing to choose between — the text directory says why.
        return app.model_overview()
    chosen = await Picker(
        [PickerScope("configured models", items)],
        title="Select model",
        variant_hint="effort",
    ).run()
    if chosen is None:
        return None
    connection_id, model_id = chosen.value
    # Commit model and effort together (dsh's pairing). A route without a
    # ladder resets to auto: carrying a named effort onto a model that
    # rejects it would fail the switch.
    return await _switch_model_report(
        app,
        connection_id or None,
        model_id,
        reasoning_effort=str(chosen.variant) if chosen.variant else "auto",
        change_effort=True,
    )


async def _cmd_model(app, args: str) -> str | None:
    wanted = args.strip()
    if not wanted:
        if app.reader.interactive:
            return await _model_via_picker(app)
        return app.model_overview()
    connection, separator, model = wanted.partition(" ")
    if not separator:
        model = connection
        connection = ""
    return await _switch_model_report(app, connection.strip() or None, model.strip())


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


_PERMISSION_CHOICES: dict[str, str | None] = {
    "ask": "ask",
    "read-only": "read_only",
    "read_only": "read_only",
    "full-access": "full_access",
    "full_access": "full_access",
    "inherit": None,
    "default": None,
}


async def _cmd_permissions(app, args: str) -> str | None:
    wanted = args.strip().casefold()
    if not wanted:
        return app.access_status()
    if wanted not in _PERMISSION_CHOICES:
        return "usage: /permissions [ask|read-only|full-access|inherit]"
    try:
        return await app.set_access_preset(_PERMISSION_CHOICES[wanted])
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


async def _cmd_rename(app, args: str) -> str | None:
    title = args.strip()
    if not title:
        return "usage: /rename <new title>"
    try:
        thread = app.thread_client.rename_thread(title)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"rename failed: {exc}"
    return f"session renamed to {thread.title}"


async def _cmd_delete(app, args: str) -> str | None:
    target = args.strip()
    if not target:
        return "usage: /delete <full session id> (see /resume all)"
    # Deletion is irreversible, so no prefix expansion here: the exact id
    # must be typed (tab completion fills it in).
    try:
        app.thread_client.delete_session(target)
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)
    return f"deleted session {target}"


async def _cmd_retry(app, args: str) -> str | None:
    if args.strip():
        return "usage: /retry (re-runs this Session's last finished Turn)"
    turn = app.thread_client.last_terminal_turn()
    if turn is None:
        return "no finished Turn to retry"
    try:
        app.thread_client.retry_turn(turn.id)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"retry failed: {exc}"
    if not app.reader.interactive:
        # Piped runs drain the retried Turn before the next stdin line —
        # the same determinism plain messages get from the REPL loop.
        await app.thread_client.wait_until_idle()
    head = turn.prompt.strip().splitlines()[0] if turn.prompt.strip() else ""
    return f"retrying: {_fit_cells(head, _RESUME_TITLE_CELLS)}"


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
            "list this directory's sessions / resume one (id prefix ok)",
            _cmd_resume,
            arguments=_session_ids,
        ),
        Command(
            "rename",
            "/rename <title>",
            "rename this session",
            _cmd_rename,
        ),
        Command(
            "delete",
            "/delete <id>",
            "permanently delete a stored session",
            _cmd_delete,
            arguments=_session_ids,
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
            arguments=_effort_levels,
        ),
        Command(
            "permissions",
            "/permissions [preset]",
            "show or set this Session's tool access",
            _cmd_permissions,
            arguments=_permission_presets,
        ),
        Command(
            "transcript",
            "/transcript [normal|verbose|summary]",
            "show or switch transcript detail (ctrl-o cycles)",
            _cmd_transcript,
            arguments=_transcript_modes,
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
        Command(
            "retry",
            "/retry",
            "re-run the last finished Turn with the current model",
            _cmd_retry,
        ),
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
