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
    2. project  ``<workspace>/.deepcode/hooks.json``
    3. project  ``<workspace>/.claude/settings.json``  (Claude-Code-compatible)

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


@dataclass(slots=True)
class Handler:
    """One discovered, runnable hook: an external command bound to an event."""

    event_name: str
    matcher: str | None
    command: str
    timeout_sec: int
    source: str  # "user" | "project" — for reporting only
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
        events = _load_hook_events(path, warnings)
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


def _load_hook_events(path: Path, warnings: list[str]) -> dict | None:
    """Read one config file and return its ``hooks`` object (or ``None``)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        warnings.append(f"failed to read hooks config {path}: {exc}")
        return None
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return None
    return hooks


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
            warnings.append(f"skipping {htype!r} hook in {path}: only command hooks supported")
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
            timeout_sec = max(1, int(timeout)) if timeout is not None else _DEFAULT_TIMEOUT_SEC
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
                status_message=status_message if isinstance(status_message, str) else None,
            )
        )
        order += 1
    return order
