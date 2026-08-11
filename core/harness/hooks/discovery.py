"""Hook discovery — parse ``hooks.json`` into runnable handlers (C3).

Adapted from the reference agent's ``hooks/src/engine/discovery.rs``. The
enterprise-only machinery there (MDM / managed requirements / trust-hash
gating) does not apply to DeepCode and is deliberately left out — what remains
is the config shape both agents share:

    {
      "hooks": {
        "PreToolUse": [
          { "matcher": "Bash",
            "hooks": [ { "type": "command", "command": "...", "timeout": 60 } ] }
        ]
      }
    }

Sources are read lowest-precedence first so ``display_order`` — which fixes the
fold order when several hooks fire for one event — is stable and deterministic:

    1. user     ``~/.deepcode/hooks.json``
    2. user-mcp ``~/.deepcode/hooks_config.json``  (deepcode-hooks MCP list format)
    3. project  ``<workspace>/.deepcode/hooks.json``
    4. project  ``<workspace>/.claude/settings.json``  (Claude-Code-compatible)

Only ``type: command`` handlers are supported; ``prompt`` / ``agent`` handlers
and ``async: true`` are skipped with a warning (the reference does the same).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.harness.hooks.events import (
    HOOK_EVENT_NAMES,
    matcher_applies_to_event,
    validate_matcher,
)

_DEFAULT_TIMEOUT_SEC = 600

# deepcode-hooks MCP stores camelCase event names; core uses the reference
# agent's PascalCase names. Keys are matched case-insensitively via .lower().
_MCP_EVENT_ALIASES: dict[str, str] = {
    "sessionstart": "SessionStart",
    "sessionend": "SessionEnd",
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "userpromptsubmit": "UserPromptSubmit",
    "permissionrequest": "PermissionRequest",
    "precompact": "PreCompact",
    "postcompact": "PostCompact",
    "subagentstart": "SubagentStart",
    "subagentstop": "SubagentStop",
    "stop": "Stop",
}


@dataclass(slots=True)
class Handler:
    """One discovered, runnable hook: an external command bound to an event."""

    event_name: str
    matcher: str | None
    command: str
    timeout_sec: int
    source: str  # "user" | "user-mcp" | "project" — for reporting only
    source_path: str
    display_order: int
    status_message: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    handlers: list[Handler]
    warnings: list[str]


def _hook_source_files(workspace: str, home: str | None) -> list[tuple[Path, str]]:
    """The (path, source-label) list, lowest precedence first."""
    home_dir = Path(home) if home is not None else Path.home()
    ws = Path(workspace)
    return [
        (home_dir / ".deepcode" / "hooks.json", "user"),
        (home_dir / ".deepcode" / "hooks_config.json", "user-mcp"),
        (ws / ".deepcode" / "hooks.json", "project"),
        (ws / ".claude" / "settings.json", "project"),
    ]


def discover_hooks(workspace: str, home: str | None = None) -> DiscoveryResult:
    """Discover all configured hook handlers for ``workspace``.

    Returns a :class:`DiscoveryResult`; an empty ``handlers`` list means the
    feature is dormant (no config files), which callers use to skip hook wiring
    entirely so a project with no hooks pays zero cost.
    """
    handlers: list[Handler] = []
    warnings: list[str] = []
    order = 0
    for path, source in _hook_source_files(workspace, home):
        events = _load_hook_events(path, warnings, source)
        if not events:
            continue
        for event_name, groups in events.items():
            if event_name not in HOOK_EVENT_NAMES:
                continue  # ignore unknown event keys, don't warn (forward-compat)
            for group in groups:
                order = _append_group(
                    handlers, warnings, event_name, group, source, path, order
                )
    return DiscoveryResult(handlers=handlers, warnings=warnings)


def _load_hook_events(path: Path, warnings: list[str], source: str) -> dict | None:
    """Read one config file and return its ``hooks`` object (or ``None``).

    Two shapes are accepted:

    - Claude-Code dict format (``{"hooks": {"EventName": [...]}}``) — any source.
    - deepcode-hooks MCP list format (``{"hooks": [...]}``) — **only** from the
      ``user-mcp`` source (``~/.deepcode/hooks_config.json``). A list shape in
      any other source is rejected with a warning so an accidental shape
      mismatch cannot silently disable hooks.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        warnings.append(f"failed to read hooks config {path}: {exc}")
        return None
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if isinstance(hooks, dict):
        return hooks  # Claude-Code format
    if isinstance(hooks, list):
        # deepcode-hooks MCP list format (hooks_config.json)
        if source != "user-mcp":
            warnings.append(
                f"ignoring list-shaped hooks in {path}: only "
                "~/.deepcode/hooks_config.json supports the deepcode-hooks "
                "list format"
            )
            return None
        return _mcp_hooks_to_events(hooks, warnings, path)
    return None


def _mcp_hooks_to_events(mcp_hooks: list, warnings: list[str], path: Path) -> dict:
    """Convert the deepcode-hooks MCP ``hooks`` list to the events-dict shape.

    Each entry: ``{name, event, handler, type, priority, timeout, enabled,
    matcher, ...}``.  Entries are validated explicitly — a malformed entry is
    reported in ``warnings`` and skipped, never silently dropped.  Only
    ``shell`` / ``node`` handlers are kept (they run as plain commands);
    ``python``-typed snippets are skipped with a warning.  Within one event
    the groups are ordered by ``priority`` (highest first; stable so equal
    priorities keep declaration order).
    """
    events: dict[str, list] = {}
    for hook in mcp_hooks:
        if not isinstance(hook, dict):
            warnings.append(f"skipping non-object hook entry in {path}")
            continue
        name = hook.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"skipping hook without a name in {path}")
            continue
        if hook.get("enabled") is False:
            continue
        event = hook.get("event")
        if not isinstance(event, str) or not event.strip():
            warnings.append(f"skipping hook {name!r} without an event in {path}")
            continue
        canonical = _MCP_EVENT_ALIASES.get(event.lower(), event)
        if canonical not in HOOK_EVENT_NAMES:
            warnings.append(
                f"skipping hook {name!r} with unknown event {event!r} in {path}"
            )
            continue
        handler = hook.get("handler")
        if not isinstance(handler, str) or not handler.strip():
            warnings.append(f"skipping hook {name!r} without a handler in {path}")
            continue
        htype = hook.get("type", "shell")
        if htype not in ("shell", "node"):
            warnings.append(
                f"skipping {htype!r} hook {name!r} in {path}: "
                "only shell/node handlers are runnable as commands"
            )
            continue
        timeout = hook.get("timeout")
        try:
            timeout_sec = max(1, int(timeout)) if timeout is not None else None
        except (TypeError, ValueError):
            warnings.append(
                f"ignoring invalid timeout {timeout!r} for hook {name!r} in {path}"
            )
            timeout_sec = None
        raw_matcher = hook.get("matcher")
        matcher = (
            raw_matcher if isinstance(raw_matcher, str) and raw_matcher.strip() else "*"
        )
        priority = hook.get("priority", 0)
        try:
            priority_int = int(priority)
        except (TypeError, ValueError):
            warnings.append(
                f"ignoring invalid priority {priority!r} for hook {name!r} in {path}"
            )
            priority_int = 0
        events.setdefault(canonical, []).append(
            {
                "matcher": matcher,
                "priority": priority_int,
                "hooks": [
                    {
                        "type": "command",
                        "command": handler,
                        **({"timeout": timeout_sec} if timeout_sec is not None else {}),
                    }
                ],
            }
        )
    # Higher ``priority`` runs first (stable sort keeps equal priorities in
    # declaration order); the transient key is dropped before _append_group.
    for groups in events.values():
        groups.sort(key=lambda group: group.get("priority", 0), reverse=True)
        for group in groups:
            group.pop("priority", None)
    return events


def _append_group(
    handlers: list[Handler],
    warnings: list[str],
    event_name: str,
    group: object,
    source: str,
    path: Path,
    order: int,
) -> int:
    """Append every command handler in one matcher-group; return the next order."""
    if not isinstance(group, dict):
        warnings.append(f"skipping non-object hook group for {event_name} in {path}")
        return order
    raw_matcher = group.get("matcher")
    matcher = matcher_applies_to_event(
        event_name, raw_matcher if isinstance(raw_matcher, str) else None
    )
    if matcher is not None:
        err = validate_matcher(matcher)
        if err is not None:
            warnings.append(f"invalid matcher {matcher!r} in {path}: {err}")
            return order
    for handler in group.get("hooks", []) or []:
        if not isinstance(handler, dict):
            continue
        htype = handler.get("type", "command")
        if htype != "command":
            warnings.append(
                f"skipping {htype!r} hook in {path}: only command hooks supported"
            )
            continue
        if handler.get("async"):
            warnings.append(f"skipping async hook in {path}: async hooks not supported")
            continue
        command = handler.get("command")
        if not isinstance(command, str) or not command.strip():
            warnings.append(f"skipping empty hook command in {path}")
            continue
        timeout = handler.get("timeout")
        try:
            timeout_sec = (
                max(1, int(timeout)) if timeout is not None else _DEFAULT_TIMEOUT_SEC
            )
        except (TypeError, ValueError):
            timeout_sec = _DEFAULT_TIMEOUT_SEC
        status_message = handler.get("statusMessage") or handler.get("status_message")
        handlers.append(
            Handler(
                event_name=event_name,
                matcher=matcher,
                command=command,
                timeout_sec=timeout_sec,
                source=source,
                source_path=str(path),
                display_order=order,
                status_message=status_message
                if isinstance(status_message, str)
                else None,
            )
        )
        order += 1
    return order
