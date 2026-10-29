"""Project hook approval: a digest of the hook declarations a project ships.

A project's ``.deepcode/hooks.json`` and ``.claude/settings.json`` are
workspace-supplied commands. Trusting a project lets its hooks run, but a later
change to those declarations (a ``git pull`` that adds or edits a hook) should
not run silently: the approval is tied to a digest of the declarations, and a
changed digest skips the project's hooks until someone approves it again.

What the digest covers, and what it does not:

* It covers the *declarations*: per project hook file, every event name and,
  per matcher group, the matcher and each handler's ``type``, ``command``,
  ``timeout``, ``async`` and ``priority``. Key order, whitespace and other keys
  (descriptions, status messages, unrelated settings) do not change it.
* It does **not** cover anything a command runs. ``sh .deepcode/check.sh``
  keeps its approval when ``check.sh`` changes; approving a hook approves a
  command line, not the files that command reads.
* Files are named by their path relative to the workspace, so the same
  declarations give the same digest through a symlink, from a git worktree, or
  after the folder moves.

The user's own ``~/.deepcode/hooks.json`` is never part of the digest and is
never gated. The approved digest is stored on the Project record; see
``core.application.project_service`` for when it is recorded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

# Markers for a project hook file without usable declarations. Each differs
# from any canonical JSON, so a file appearing, breaking or disappearing
# changes the digest.
MISSING = b"<missing>"
UNREADABLE = b"<unreadable>"
NO_HOOKS = b"<no-hooks>"


@dataclass(frozen=True, slots=True)
class ProjectHookApproval:
    """The project hook declarations a session may run.

    Project hooks run only when the digest of their current declarations
    equals ``approved_digest``; ``None`` approves nothing.
    """

    approved_digest: str | None


def canonical_declaration(data: object) -> bytes:
    """Order-stable bytes for the declarations in one parsed hooks file."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return NO_HOOKS
    reduced: dict[str, list] = {}
    for event_name in sorted(hooks):
        groups = hooks[event_name]
        if not isinstance(groups, list):
            continue
        reduced[event_name] = [
            _canonical_group(group) for group in groups if isinstance(group, dict)
        ]
    return json.dumps(reduced, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_group(group: dict) -> dict:
    """Reduce one matcher group to the fields that decide what runs."""
    out: dict[str, object] = {}
    matcher = group.get("matcher")
    if isinstance(matcher, str):
        out["matcher"] = matcher
    handlers = group.get("hooks")
    if isinstance(handlers, list):
        out["hooks"] = [
            {
                key: handler[key]
                for key in ("type", "command", "timeout", "async", "priority")
                if key in handler
            }
            for handler in handlers
            if isinstance(handler, dict)
        ]
    return out


def digest_declarations(entries: Iterable[tuple[str, bytes]]) -> str:
    """sha256 over ``(relative path, canonical declaration)`` pairs."""
    hasher = hashlib.sha256()
    for label, canonical in sorted(entries):
        hasher.update(label.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(canonical)
        hasher.update(b"\x00")
    return hasher.hexdigest()


def declaration_digest(workspace: str) -> str:
    """The digest of ``workspace``'s project hook declarations right now."""
    from core.harness.hooks.discovery import project_declaration_digest

    return project_declaration_digest(workspace)


__all__ = [
    "ProjectHookApproval",
    "canonical_declaration",
    "declaration_digest",
    "digest_declarations",
]
