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

# The product logo as a terminal mark: the thick bracket "C" whose open side
# dissolves into circuit traces (assets/logo.png), at the one size a
# scrollback can afford. Rows are printed independently and left-aligned —
# nothing below depends on a column matching a row above, so a terminal that
# renders block glyphs double-wide (an East Asian "ambiguous = wide" setting)
# shifts the traces without ever breaking a frame. There is no frame: the
# banner is borderless, as in dsh.
# Each row is ``(bracket, traces)``: the bracket carries the brand ramp top
# to bottom, the traces are recessed, exactly as the artwork reads.
BRAND_ART = (
    ("██████", "  ·──○"),
    ("██", "       ○──·"),
    ("██████", "  ·──○"),
)
BRAND_ART_WIDTH = 16  # narrower terminals fall back to the one-line wordmark
BRAND_TAGLINE = "open agentic coding"

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

# -- plan cards -------------------------------------------------------------
# The plan tool's checklist (dsh's todo row): one glyph per step status, all
# line-leading so a width surprise costs alignment, never structure.
PLAN_LABEL = "Plan"
PLAN_STEP_DONE = "✓"
PLAN_STEP_ACTIVE = "▸"
PLAN_STEP_PENDING = "◦"
PLAN_ACTIVE_STYLE = ACCENT

# -- status / meta ----------------------------------------------------------
META_STYLE = DIM
ERROR_STYLE = f"bold {ERROR}"
INTERRUPT_HINT = "esc interrupts · ctrl+o transcript detail"
DONE_OK = "✓"
DONE_ERR = "✗"

# -- approvals --------------------------------------------------------------
APPROVAL_STYLE = WARNING
APPROVAL_PROMPT = "y once · a session · n deny"

# -- pickers (prompt_toolkit dialect; it has no dim attribute, so the
# hierarchy is bold titles over plain detail, accent on the selection) ------
PICKER_POINTER = "❯"
PICKER_TITLE_STYLE_PTK = "bold"
PICKER_SELECTED_STYLE_PTK = "bold fg:ansicyan"

# -- status line (prompt_toolkit dialect) -----------------------------------
# The status line is the one animated surface: a spinner, the activity's own
# word under dsh's glare sweep, then recessed detail. Rules live here as
# data so ``input`` can hand them to prompt_toolkit without knowing what any
# of them mean.
#
# ``bottom-toolbar`` ships as ``reverse`` — a solid inverted bar across the
# terminal. That is the opposite of the borderless look everything else
# follows, and it swallows the sweep (an inverted bright band reads as a
# hole), so the class is reset here and the line carries its own colour.
STATUS_STYLE_RULES = {
    "bottom-toolbar": "noreverse",
    "bottom-toolbar.text": "noreverse",
    "status": "fg:ansibrightblack",
    "status.spinner": "fg:ansicyan",
    "status.label": "fg:ansicyan bold",
    "status.glare": "fg:ansiwhite bold",
    "status.detail": "fg:ansibrightblack",
}
STATUS_BASE_PTK = "class:status"
STATUS_SPINNER_PTK = "class:status.spinner"
STATUS_LABEL_PTK = "class:status.label"
STATUS_GLARE_PTK = "class:status.glare"
STATUS_DETAIL_PTK = "class:status.detail"


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


def wordmark_markup() -> str:
    """``DeepCode`` alone — the mark above the logo art already says ``✳``."""
    if supports_truecolor():
        return f"[bold]{gradient_markup(BRAND_NAME)}[/bold]"
    return f"[bold {ACCENT}]{BRAND_NAME}[/]"


def brand_art_markup() -> list[str]:
    """The logo as rich markup, one string per row.

    The ramp runs down the rows rather than across each one: the bracket is
    a single shape, and a per-row horizontal gradient would restart the
    ramp three times and read as stripes. Without truecolor every row is
    flat ``ACCENT`` — the shape carries the brand, the colour only dresses
    it.
    """
    truecolor = supports_truecolor()
    stops = BRAND_GRADIENT
    rows: list[str] = []
    for index, (bracket, traces) in enumerate(BRAND_ART):
        if truecolor:
            red, green, blue = stops[min(index, len(stops) - 1)]
            bracket_style = f"#{red:02x}{green:02x}{blue:02x}"
        else:
            bracket_style = ACCENT
        rows.append(f"[{bracket_style}]{bracket}[/][{DIM}]{traces}[/]")
    return rows
