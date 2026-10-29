"""Hook discovery — parse ``hooks.json`` into runnable handlers (C3).

Adapted from the reference agent's ``hooks/src/engine/discovery.rs``. The
enterprise-only machinery there (MDM / managed requirements) does not apply to
DeepCode and is left out. Project hooks are gated by a digest of their
declarations instead (see :mod:`core.harness.hooks.trust`): given a
:class:`ProjectHookApproval`, project hooks whose current digest is not the
approved one are skipped. The config shape both agents share:

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
from core.harness.hooks.trust import (
    MISSING,
    UNREADABLE,
    ProjectHookApproval,
    canonical_declaration,
    digest_declarations,
)

_DEFAULT_TIMEOUT_SEC = 600
_SESSION_END_DEFAULT_TIMEOUT_SEC = 2
_SESSION_END_MAX_TIMEOUT_SEC = 60


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
    # Digest of the project hook declarations these handlers were read from.
    project_digest: str = ""
    # True when project hooks exist but were left out as unapproved.
    project_hooks_skipped: bool = False


def _hook_source_files(workspace: str, home: str | None) -> list[tuple[Path, str]]:
    """The (path, source-label) list, lowest precedence first."""
    home_dir = Path(home) if home is not None else Path.home()
    ws = Path(workspace)
    return [
        (home_dir / ".deepcode" / "hooks.json", "user"),
        (ws / ".deepcode" / "hooks.json", "project"),
        (ws / ".claude" / "settings.json", "project"),
    ]


def discover_hooks(
    workspace: str,
    home: str | None = None,
    *,
    project_hook_approval: ProjectHookApproval | None = None,
) -> DiscoveryResult:
    """Discover all configured hook handlers for ``workspace``.

    Returns a :class:`DiscoveryResult`; an empty ``handlers`` list means the
    feature is dormant (no config files), which callers use to skip hook wiring
    entirely so a project with no hooks pays zero cost.

    With ``project_hook_approval``, project hooks run only if the digest of the
    declarations read here equals the approved digest. Otherwise they are
    left out and one warning says so; the user's own hooks are never gated.
    The digest is taken from the same bytes the handlers are built from, so a
    file edited mid-discovery cannot slip in under an older approval.
    """
    warnings: list[str] = []
    loaded: list[tuple[Path, str, dict | None]] = []
    declarations: list[tuple[str, bytes]] = []
    workspace_root = Path(workspace)
    for path, source in _hook_source_files(workspace, home):
        events, canonical = _load_hook_events(path, warnings)
        if source == "project":
            label = path.relative_to(workspace_root).as_posix()
            declarations.append((label, canonical))
        loaded.append((path, source, events))
    digest = digest_declarations(declarations)

    handlers: list[Handler] = []
    order = 0
    for path, source, events in loaded:
        if not events:
            continue
        for event_name, groups in events.items():
            if event_name not in HOOK_EVENT_NAMES:
                continue  # ignore unknown event keys, don't warn (forward-compat)
            for group in groups:
                order = _append_group(
                    handlers, warnings, event_name, group, source, path, order
                )

    skipped = False
    if (
        project_hook_approval is not None
        and project_hook_approval.approved_digest != digest
        and any(handler.source == "project" for handler in handlers)
    ):
        handlers = [handler for handler in handlers if handler.source != "project"]
        skipped = True
        warnings.append(
            "Project hooks were not run: the hooks declared in .deepcode/"
            "hooks.json or .claude/settings.json are new or changed since they "
            "were approved. Run `deepcode hooks approve` in this project to "
            "enable them."
        )
    return DiscoveryResult(
        handlers=handlers,
        warnings=warnings,
        project_digest=digest,
        project_hooks_skipped=skipped,
    )


def project_declaration_digest(workspace: str) -> str:
    """Digest of ``workspace``'s project hook declarations, without warnings."""
    workspace_root = Path(workspace)
    declarations = [
        (path.relative_to(workspace_root).as_posix(), _load_hook_events(path, [])[1])
        for path, source in _hook_source_files(workspace, None)
        if source == "project"
    ]
    return digest_declarations(declarations)


def _load_hook_events(path: Path, warnings: list[str]) -> tuple[dict | None, bytes]:
    """Read one config file: its ``hooks`` object and canonical declarations."""
    if not path.is_file():
        return None, MISSING
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"failed to read hooks config {path}: {exc}")
        return None, UNREADABLE
    canonical = canonical_declaration(data)
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return None, canonical
    return hooks, canonical


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
        default_timeout = (
            _SESSION_END_DEFAULT_TIMEOUT_SEC
            if event_name == "SessionEnd"
            else _DEFAULT_TIMEOUT_SEC
        )
        try:
            timeout_sec = (
                max(1, int(timeout)) if timeout is not None else default_timeout
            )
        except (TypeError, ValueError):
            timeout_sec = default_timeout
        if event_name == "SessionEnd":
            timeout_sec = min(timeout_sec, _SESSION_END_MAX_TIMEOUT_SEC)
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
