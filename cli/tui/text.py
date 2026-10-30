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

Prose wraps the way CJK typesetting does (:func:`wrap_text`): a line may also
end between two CJK characters, which have no spaces between them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from rich.cells import cell_len, get_character_cell_size, set_cell_size
from rich.text import Text

_ELLIPSIS = "…"


# Escape sequences a program writes for a terminal (colours, cursor moves,
# window titles, keypad modes), including ones a preview cut short, and the
# control characters left over. Shown as text they are noise; cut short they
# leak into the terminal.
_TERMINAL_ESCAPE = re.compile(
    r"\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?"  # OSC, ended or cut short
    r"|\x1b[P_^X][^\x1b\n]*(?:\x1b\\)?"  # DCS, APC, PM and SOS strings
    r"|\x1b\[[0-?]*[ -/]*[@-~]?"  # CSI, ended or cut short
    r"|\x1b[ -/]*[0-~]?"  # every other escape
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"  # other controls; tab, LF, CR stay
)


def terminal_text(text: str) -> str:
    """Program output as a terminal would have left it on screen.

    Escape sequences go, a carriage return overwrites its line (a progress
    bar keeps only its last frame) and tabs become spaces, so the width Rich
    measures is the width shown.
    """
    lines = _TERMINAL_ESCAPE.sub("", text).split("\n")
    return "\n".join(
        line.rstrip("\r").rsplit("\r", 1)[-1].expandtabs(4) for line in lines
    )


# Spaces separate words. At a line break they stay at the end of the line,
# past the edge, and are dropped.
_SPACES = " \u3000"
# Kinsoku, the basic rule of CJK line breaking: closing punctuation never
# starts a line and opening punctuation never ends one. A straight quote can
# be either, so it stays with the characters on both sides.
_NO_LINE_START = frozenset("，。、；：！？）」』】》〉〕］｝〗”’…ー々.,;:!?)]}%\"'")
_NO_LINE_END = frozenset("（「『【《〈〔［｛〖“‘([{\"'")


def _may_break(before: str, after: str) -> bool:
    """Whether a line may end between the characters ``before`` and ``after``."""
    if after in _SPACES:
        return False
    if before in _SPACES:
        return True
    if after in _NO_LINE_START or before in _NO_LINE_END:
        return False
    before_cells = get_character_cell_size(before)
    after_cells = get_character_cell_size(after)
    if not before_cells or not after_cells:
        return False  # a combining mark or a joiner stays with its neighbours
    return before_cells == 2 or after_cells == 2


def wrap_offsets(text: str, width: int) -> list[int]:
    """Where lines of at most ``width`` cells start in ``text``, after the first.

    A line ends after a space or next to a CJK character, never inside a word
    of other text: Rich breaks only at spaces, so CJK text either jumped whole
    to the next line or was cut at the edge mid-word. Spaces at a break stay
    on the line they end; spaces that begin the text stay with its first word.
    A word wider than a whole line is cut at the edge.
    """
    width = max(1, width)
    offsets: list[int] = []
    used = 0  # cells on the current line, its trailing spaces included
    start = 0
    lead = len(text) - len(text.lstrip(_SPACES))
    ends = [i for i in range(lead + 1, len(text)) if _may_break(text[i - 1], text[i])]
    for end in [*ends, len(text)]:
        unit = text[start:end]
        body = cell_len(unit.rstrip(_SPACES))
        if used and used + body > width:
            offsets.append(start)
            used = 0
        if body > width:
            for index, char in enumerate(unit):
                size = get_character_cell_size(char)
                if used and used + size > width and char not in _SPACES:
                    offsets.append(start + index)
                    used = 0
                used += size
        else:
            used += cell_len(unit)
        start = end
    return offsets


def wrap_text(text: Text, width: int) -> list[Text]:
    """``text`` as lines of at most ``width`` cells, styles kept (:func:`wrap_offsets`)."""
    lines = list(text.divide(wrap_offsets(text.plain, width)))
    for line in lines:
        line.rstrip()
    return lines


def fit_head(text: str, width: int, *, mark: bool = True) -> str:
    """Keep the head of ``text`` within ``width`` terminal cells.

    A cut ends in ``…`` unless ``mark`` is false (the caller marks it).
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    kept = set_cell_size(text, max(1, width - 1) if mark else width).rstrip()
    return kept + _ELLIPSIS if mark else kept


def fit_tail(text: str, width: int, *, mark: bool = True) -> str:
    """Keep the tail of ``text`` within ``width`` terminal cells.

    A cut starts with ``…`` unless ``mark`` is false (the caller marks it).
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    budget = max(1, width - 1) if mark else width
    cut = len(text)
    while cut > 0 and cell_len(text[len(text) - cut :]) > budget:
        cut -= 1
    kept = text[len(text) - cut :]
    return _ELLIPSIS + kept if mark else kept.lstrip()


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
            # Display form: forward slashes on every platform, so a path reads
            # the same on Windows as it does in the transcript examples (and in
            # what the user types: ``tools/foo.py``).
            return relative.as_posix() or "."
    return short_path(candidate)
