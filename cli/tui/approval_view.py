"""The approval card: enough to decide on, and the keys to answer with.

An approval used to show the tool's name and an internal reason. A user
asked to allow ``rm -rf build && echo removed`` or an edit needs to see the
whole command and where it runs, or the change itself, before answering.

Pure presentation over the approval view the service sends
(``request.toolName``, ``request.arguments``, ``request.reason``,
``request.sessionScope``); the only thing it reads besides is the file an
edit would change, to show the change as a diff.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Mapping
from pathlib import Path

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.text import Text

from cli.tui import theme
from cli.tui.text import workspace_path
from core.harness.approval_scope import requested_session_target
from core.harness.permissions import is_file_edit_tool, is_shell_tool

_DIFF_LINES = 40  # lines of a change shown in the normal transcript
_DIFF_LINES_VERBOSE = 400  # and in the verbose one
_ARGUMENT_LINES = 8  # other tools: their arguments, one per line
_ARGUMENT_CELLS = 160
_READ_LIMIT_BYTES = 1_000_000  # a larger file is not read for a diff
_CONTEXT_LINES = 2
_INDENT = 2
_BODY_INDENT = 4


def _request(approval: Mapping) -> Mapping:
    request = approval.get("request")
    return request if isinstance(request, Mapping) else {}


def _arguments(request: Mapping) -> Mapping:
    arguments = request.get("arguments")
    return arguments if isinstance(arguments, Mapping) else {}


def session_target(approval: Mapping) -> str:
    """What "a" allows, e.g. "`git status …`" or "edits in this workspace"."""

    return requested_session_target(_request(approval))


def answer_hint(approval: Mapping) -> str:
    """The keys, as the prompt and the status line show them."""

    return (
        f"y allow once · a always allow {session_target(approval)} this session"
        " · n deny"
    )


def outcome_text(status: str, approval: Mapping | None) -> str:
    """One line for the answer this terminal gave."""

    if status == "approved_once":
        return "Allowed once."
    if status == "approved_session":
        target = session_target(approval) if approval is not None else "this"
        return f"Allowed {target} for the rest of this session."
    if status == "denied":
        return "Denied."
    return f"Approval {status.replace('_', ' ')}."


def approval_card(
    approval: Mapping,
    *,
    workspace: str,
    verbose: bool = False,
) -> RenderableType:
    """The card printed when an approval is requested."""

    request = _request(approval)
    tool = str(request.get("toolName") or "tool")
    arguments = _arguments(request)
    reason = str(request.get("reason") or "").strip()
    if is_shell_tool(tool):
        title, body = "Run a command", _command_body(arguments, workspace)
    elif is_file_edit_tool(tool):
        title, body = _edit_body(tool, arguments, workspace, verbose=verbose)
    else:
        title, body = tool, _argument_body(arguments)
    heading = Text.assemble(
        ("◆ Approval needed", f"bold {theme.APPROVAL_STYLE}"),
        (" · ", theme.META_STYLE),
        (title, "bold"),
    )
    parts: list[RenderableType] = [Text(""), heading]
    if reason:
        parts.append(Padding(Text(reason), (0, 0, 0, _INDENT)))
    parts.extend(Padding(line, (0, 0, 0, _BODY_INDENT)) for line in body)
    parts.append(
        Padding(
            Text(answer_hint(approval), style=f"bold {theme.APPROVAL_STYLE}"),
            (0, 0, 0, _INDENT),
        )
    )
    return Group(*parts)


# -- commands ---------------------------------------------------------------


def _command_body(arguments: Mapping, workspace: str) -> list[Text]:
    command = ""
    for key in ("command", "cmd", "commands", "script"):
        value = arguments.get(key)
        if isinstance(value, str):
            command = value
            break
        if isinstance(value, (list, tuple)):
            command = "\n".join(str(part) for part in value)
            break
    lines: list[Text] = []
    for index, line in enumerate(command.strip().splitlines() or [""]):
        lines.append(
            Text.assemble(
                ("$ " if index == 0 else "  ", theme.META_STYLE), (line, "bold")
            )
        )
    directory = next(
        (
            value
            for key in ("cwd", "workdir", "working_directory", "directory")
            if isinstance(value := arguments.get(key), str) and value.strip()
        ),
        workspace,
    )
    lines.append(Text(f"in {_home_relative(directory)}", style=theme.META_STYLE))
    return lines


def _home_relative(path: str) -> str:
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


# -- file edits -------------------------------------------------------------


def _edit_body(
    tool: str, arguments: Mapping, workspace: str, *, verbose: bool
) -> tuple[str, list[Text]]:
    limit = _DIFF_LINES_VERBOSE if verbose else _DIFF_LINES
    path = next(
        (
            value
            for key in ("file_path", "path", "filename", "target")
            if isinstance(value := arguments.get(key), str) and value.strip()
        ),
        None,
    )
    patch = arguments.get("patch") or arguments.get("input")
    if path is None and isinstance(patch, str):
        lines = [
            line
            for line in patch.splitlines()
            if line.strip() not in {"*** Begin Patch", "*** End Patch"}
        ]
        files = sum(1 for line in lines if line.startswith("*** "))
        title = "Apply a patch" + (f" to {files} files" if files > 1 else "")
        return title, _diff_lines(lines, limit=limit, verbose=verbose)
    if path is None:
        return tool, _argument_body(arguments)
    shown = workspace_path(path, workspace)
    current = _read(path, workspace)
    new_string = arguments.get("new_string")
    old_string = arguments.get("old_string")
    if isinstance(old_string, str) and isinstance(new_string, str):
        if current is not None and old_string in current:
            count = -1 if arguments.get("replace_all") else 1
            after = current.replace(old_string, new_string, count)
            diff = _unified(current, after)
        else:
            diff = _unified(old_string, new_string)
        return f"Edit {shown}", _diff_lines(diff, limit=limit, verbose=verbose)
    content = arguments.get("content")
    if isinstance(content, str):
        if current is None:
            lines = content.splitlines()
            title = (
                f"Create {shown} · {len(lines)} line{'s' if len(lines) != 1 else ''}"
            )
            return title, _diff_lines(
                [f"+{line}" for line in lines], limit=limit, verbose=verbose
            )
        return f"Overwrite {shown}", _diff_lines(
            _unified(current, content), limit=limit, verbose=verbose
        )
    return f"{tool} {shown}", _argument_body(arguments)


def _read(path: str, workspace: str) -> str | None:
    target = Path(os.path.expanduser(path))
    if not target.is_absolute():
        target = Path(workspace) / target
    try:
        if not target.is_file() or target.stat().st_size > _READ_LIMIT_BYTES:
            return None
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _unified(before: str, after: str) -> list[str]:
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        lineterm="",
        n=_CONTEXT_LINES,
    )
    # The ---/+++ header names two sides the title already named.
    return [line for line in diff if not line.startswith(("---", "+++"))]


def _diff_lines(lines: list[str], *, limit: int, verbose: bool) -> list[Text]:
    if not lines:
        return [Text("(no change)", style=theme.META_STYLE)]
    rendered = [_diff_line(line) for line in lines[:limit]]
    hidden = len(lines) - limit
    if hidden > 0:
        more = f"… {hidden} more line{'s' if hidden != 1 else ''}"
        if not verbose:
            more += " (/transcript verbose shows up to 400)"
        rendered.append(Text(more, style=theme.META_STYLE))
    return rendered


def _diff_line(line: str) -> Text:
    if line.startswith("+") and not line.startswith("+++"):
        style = theme.SUCCESS
    elif line.startswith("-") and not line.startswith("---"):
        style = theme.ERROR
    elif line.startswith(("@@", "*** ")):
        style = theme.ACCENT
    else:
        style = theme.META_STYLE
    return Text(
        line.replace("\t", "    "), style=style, no_wrap=True, overflow="ellipsis"
    )


# -- other tools ------------------------------------------------------------


def _argument_body(arguments: Mapping) -> list[Text]:
    lines: list[Text] = []
    for key, value in list(arguments.items())[:_ARGUMENT_LINES]:
        text = value if isinstance(value, str) else repr(value)
        text = " ".join(str(text).split())
        if len(text) > _ARGUMENT_CELLS:
            text = text[: _ARGUMENT_CELLS - 1] + "…"
        lines.append(Text.assemble((f"{key}: ", theme.META_STYLE), text))
    extra = len(arguments) - _ARGUMENT_LINES
    if extra > 0:
        lines.append(Text(f"… {extra} more", style=theme.META_STYLE))
    return lines
