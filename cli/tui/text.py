"""Text fitting for the TUI's terminal surfaces — cells, not characters.

Two rules the rendering surfaces share, kept in one place so the renderer,
the command statuses, and the pickers cannot drift apart:

- **Fit by cells.** ``len`` counts code points; a terminal counts columns,
  and a CJK glyph occupies two of them. Truncating by ``len`` overflows the
  line and wraps into an unindented mess, so every budget here is a cell
  budget (rich's ``cell_len``/``set_cell_size`` own the width table).
- **Cut the uninformative end.** A command says what it is at the HEAD
  (``pytest -q …``); a path says what it is at the TAIL
  (``…/core/tui/renderer.py``). Which end survives is the caller's call,
  and both are one function away.

Paths get the same treatment they get in a shell prompt: relative to the
workspace the user launched in, ``~`` for home, absolute only when it is
neither.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.cells import cell_len, set_cell_size

_ELLIPSIS = "…"


def fit_head(text: str, width: int) -> str:
    """Keep the head of ``text`` within ``width`` terminal cells."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    return set_cell_size(text, max(1, width - 1)).rstrip() + _ELLIPSIS


def fit_tail(text: str, width: int) -> str:
    """Keep the tail of ``text`` within ``width`` terminal cells."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    budget = max(1, width - 1)
    cut = len(text)
    while cut > 0 and cell_len(text[len(text) - cut :]) > budget:
        cut -= 1
    return _ELLIPSIS + text[len(text) - cut :]


def short_path(path: str) -> str:
    """``~``-fold a path that lives under the user's home directory."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    return f"~{path[len(home) :]}" if path.startswith(home + os.sep) else path


def workspace_path(path: str, workspace: str | None) -> str:
    """The shortest honest way to name ``path`` to someone in ``workspace``.

    Inside the workspace it is the relative path (what the user would type);
    outside it falls back to the ``~``-folded absolute path. A value that is
    already relative keeps its own shape, minus the ``./`` a tool argument
    often carries — ``./a/b.py`` and ``a/b.py`` name the same file and only
    one of them is worth the columns. Never guesses beyond that: anything
    that is not a path — a command line, a search pattern — comes back
    untouched.
    """
    if not path:
        return path
    if path.startswith("./") and len(path) > 2:
        return path[2:]
    if not path.startswith(("/", "~")):
        return path
    candidate = os.path.expanduser(path)
    if workspace:
        try:
            relative = Path(candidate).relative_to(Path(workspace))
        except ValueError:
            pass
        else:
            return str(relative) or "."
    return short_path(candidate)
