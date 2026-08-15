"""Visual vocabulary for the DeepCode TUI — one place, no magic strings.

Every color, glyph, and prefix the renderer uses lives here, so the whole
look can be tuned without touching rendering logic. The design rules are
borrowed from dsh's terminal palette and adapted to DeepCode's own brand:

- **Roles, not colors.** Consumers name a role (``ACCENT``, ``DIM``,
  ``SUCCESS``…); what the role maps to is this module's business.
- **Standard ANSI colors only**, so the user's terminal theme remaps
  everything and the TUI reads correctly on light and dark schemes alike.
  ``dim`` is the style attribute (SGR 2), never a fixed grey: a fixed grey
  renders HEAVIER than the default foreground on many light themes.
- **One truecolor exception**: the startup banner's brand gradient, which
  degrades to flat ``ACCENT`` when the terminal lacks truecolor.
- DeepCode keeps its own glyph signature — the ``✳`` brand mark, the ``⎿``
  result elbow, the two-line tool rhythm — where dsh uses a single-header
  card. The shared grammar is status-colored markers over recessed bodies.
"""

from __future__ import annotations

import os

# -- brand ------------------------------------------------------------------
BRAND_MARK = "✳"
BRAND_NAME = "DeepCode"
BRAND = f"{BRAND_MARK} {BRAND_NAME}"
# The banner gradient (DeepCode's sky-cyan ramp, matching the Desktop and
# logo palette). Only the banner may use these; everything else is ANSI.
BRAND_GRADIENT = ((14, 165, 233), (6, 182, 212), (78, 205, 196))

# -- color roles ------------------------------------------------------------
ACCENT = "cyan"
DIM = "dim"  # SGR 2: fades relative to the terminal's own foreground
SUCCESS = "green"
WARNING = "yellow"
ERROR = "red"

# -- prompt -----------------------------------------------------------------
PROMPT = "› "
PROMPT_STYLE = f"bold {ACCENT}"  # rich dialect
PROMPT_STYLE_PTK = "bold fg:ansicyan"  # prompt_toolkit dialect of the same role
CONTINUATION = "… "


def prompt_fragments() -> list[tuple[str, str]]:
    """The input caret as prompt_toolkit formatted-text fragments."""
    return [(PROMPT_STYLE_PTK, PROMPT)]


# -- assistant text ---------------------------------------------------------
ASSISTANT_STYLE = "default"
THINKING_STYLE = DIM
THINKING_MARK = "◇"

# -- tool cards -------------------------------------------------------------
TOOL_BULLET = "●"
TOOL_RESULT_ELBOW = "  ⎿"
TOOL_RUNNING_STYLE = ACCENT
TOOL_OK_STYLE = SUCCESS
TOOL_ERR_STYLE = ERROR
TOOL_DETAIL_STYLE = DIM

# -- status / meta ----------------------------------------------------------
META_STYLE = DIM
ERROR_STYLE = f"bold {ERROR}"
INTERRUPT_HINT = "esc interrupts · ctrl+o transcript detail"
DONE_OK = "✓"
DONE_ERR = "✗"

# -- approvals --------------------------------------------------------------
APPROVAL_STYLE = WARNING
APPROVAL_PROMPT = "y once · a session · n deny"


def supports_truecolor() -> bool:
    """True when the terminal advertises 24-bit color support."""
    colorterm = os.environ.get("COLORTERM", "").lower()
    return "truecolor" in colorterm or "24bit" in colorterm


def gradient_markup(text: str) -> str:
    """Rich markup painting ``text`` across the brand gradient.

    The one sanctioned truecolor crack (the dsh rule): brand art only.
    Callers must gate on :func:`supports_truecolor` and fall back to the
    flat ``ACCENT`` role otherwise.
    """
    if len(text) == 0:
        return text
    (r0, g0, b0), (r1, g1, b1), (r2, g2, b2) = BRAND_GRADIENT
    span = max(1, len(text) - 1)
    parts: list[str] = []
    for index, char in enumerate(text):
        position = index / span
        if position <= 0.5:
            blend = position * 2
            r = round(r0 + (r1 - r0) * blend)
            g = round(g0 + (g1 - g0) * blend)
            b = round(b0 + (b1 - b0) * blend)
        else:
            blend = (position - 0.5) * 2
            r = round(r1 + (r2 - r1) * blend)
            g = round(g1 + (g2 - g1) * blend)
            b = round(b1 + (b2 - b1) * blend)
        parts.append(f"[#{r:02x}{g:02x}{b:02x}]{char}[/]")
    return "".join(parts)


def brand_markup() -> str:
    """The banner brand line: gradient when the terminal can, accent when not."""
    if supports_truecolor():
        return f"[bold]{gradient_markup(BRAND)}[/bold]"
    return f"[bold {ACCENT}]{BRAND}[/]"
