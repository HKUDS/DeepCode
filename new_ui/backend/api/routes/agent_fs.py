"""Restricted directory-browse API for the Agent Chat workspace picker.

The browser can't read the local filesystem, so a chat's workspace used to be
a blind text field. This endpoint lets the frontend render a real folder
picker — but only a *directory* listing, fenced under the user's home so it
can never be turned into a file-exfiltration primitive:

- lists sub-directories only (never file contents, never file names beyond
  what a folder tree needs);
- resolves symlinks and refuses anything that escapes ``$HOME``;
- hides dotfolders by default.

It is read-only and additive; nothing here mutates state.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_HOME = Path.home().resolve()


def _fenced(path: Path) -> Path:
    """Resolve ``path`` and require it to stay within ``$HOME``."""
    resolved = path.expanduser().resolve()
    if resolved != _HOME and _HOME not in resolved.parents:
        raise HTTPException(
            status_code=403, detail="path is outside the home directory"
        )
    return resolved


@router.get("/dirs")
async def list_dirs(path: str = ""):
    """List sub-directories of ``path`` (defaults to and fenced under $HOME)."""
    target = _fenced(Path(path)) if path else _HOME
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="not a directory")

    dirs: list[dict[str, str]] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": str(entry)})
            except OSError:
                continue  # unreadable / permission — skip quietly
    except OSError as exc:
        raise HTTPException(status_code=403, detail=f"cannot read directory: {exc}")

    parent = str(target.parent) if target != _HOME else None
    return {
        "path": str(target),
        "parent": parent,
        "home": str(_HOME),
        "dirs": dirs,
    }
