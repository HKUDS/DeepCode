"""Project hook declaration trust — sha256 digest approval store (ZCode).

ZCode's rule: a project's ``hooks.json`` is *workspace-supplied code*. Trusting
a project once must not silently trust it forever — if the declaration changes,
the approval is void and the operator must re-approve the new content.

The existing engine gate is a boolean ``project_trusted``. This module layers a
content digest on top:

* :func:`declaration_digest` hashes the canonical declaration content, so
  whitespace/key-order churn does not invalidate an approval while any real
  command/matcher/event change does.
* :class:`HookTrustStore` persists approved digests keyed by workspace in a
  small JSON file under ``deepcode_home()``. It is intentionally dependency-free
  (no SQLite) so hook wiring stays cheap.
* The gate stays **opt-in**: with no store configured, behavior is the existing
  boolean ``project_trusted`` path, byte-for-byte.

An approval is per (workspace, digest). A changed declaration produces a new
digest, so the old approval no longer matches and project hooks revert to
blocked until the operator re-approves.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_STORE_FILENAME = "hook_trust.json"
_STORE_VERSION = 1


def declaration_digest(
    workspace: str,
    *,
    home: str | None = None,
    paths: tuple[Path, ...] | None = None,
) -> str:
    """sha256 digest over the canonical project hook declarations.

    ``paths`` lets callers pass the exact discovered project files; when omitted
    the two project sources are used (user-level config is the operator's own
    machine and is never gated, so it never feeds the digest).

    Only *declaration* content is hashed — event name, matcher, command, timeout,
    priority, type — so cosmetic edits (reordering keys, reindenting) keep the
    approval while any semantic change produces a new digest. Files that are
    absent or unreadable contribute a stable marker so a file appearing/disappearing
    changes the digest.
    """
    from core.harness.hooks.discovery import _hook_source_files

    if paths is None:
        paths = tuple(
            path
            for path, source in _hook_source_files(workspace, home)
            if source == "project"
        )
    hasher = hashlib.sha256()
    for path in sorted(paths, key=str):
        hasher.update(str(path).encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(_canonical_declaration(path))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _canonical_declaration(path: Path) -> bytes:
    """Canonical, order-stable bytes for one hooks file's declarations."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Absent/unreadable: a stable per-path marker that differs from real content.
        return b"<missing>"
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return b"<no-hooks>"
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
    """Reduce one matcher-group to its declaration-relevant fields."""
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
                if isinstance(handler, dict) and key in handler
            }
            for handler in handlers
            if isinstance(handler, dict)
        ]
    return out


class HookTrustStore:
    """Persist approved project hook digests per workspace.

    Thread-safe and file-backed. Approvals are a set of digests per workspace so
    a workspace can be re-approved across restarts without re-prompting until its
    declaration actually changes.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (self._default_dir() / _STORE_FILENAME)
        self._lock = threading.Lock()

    @staticmethod
    def _default_dir() -> Path:
        from core.config import deepcode_home

        return deepcode_home()

    @property
    def path(self) -> Path:
        return self._path

    def is_approved(self, workspace: str, digest: str) -> bool:
        """True when this exact declaration digest is approved for ``workspace``."""
        with self._lock:
            return digest in self._read().get(_key(workspace), [])

    def approve(self, workspace: str, digest: str) -> None:
        """Approve one declaration digest for ``workspace`` (idempotent)."""
        with self._lock:
            data = self._read()
            approved = data.setdefault(_key(workspace), [])
            if digest not in approved:
                approved.append(digest)
                self._write(data)

    def revoke(self, workspace: str, digest: str | None = None) -> None:
        """Revoke one digest, or every approval for ``workspace`` when None."""
        with self._lock:
            data = self._read()
            key = _key(workspace)
            if key not in data:
                return
            if digest is None:
                del data[key]
            else:
                approved = [d for d in data[key] if d != digest]
                if approved:
                    data[key] = approved
                else:
                    del data[key]
            self._write(data)

    def _read(self) -> dict[str, list[str]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict) or raw.get("version") != _STORE_VERSION:
            return {}
        workspaces = raw.get("workspaces")
        if not isinstance(workspaces, dict):
            return {}
        return {
            key: [d for d in value if isinstance(d, str)]
            for key, value in workspaces.items()
            if isinstance(value, list)
        }

    def _write(self, data: dict[str, list[str]]) -> None:
        payload = {"version": _STORE_VERSION, "workspaces": data}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace so a crash mid-write never corrupts the store.
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".hook_trust.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("failed to persist hook trust store at %s", self._path)
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _key(workspace: str) -> str:
    return str(Path(workspace).resolve())


__all__ = [
    "HookTrustStore",
    "declaration_digest",
]
