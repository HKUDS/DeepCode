"""Motion for the TUI status line — pure functions of elapsed time.

dsh signals a running row with a **sweep**: a fixed-width glare band glides
over the row from off-left to off-right, washing the glyphs toward the
background as it passes, ``ease-out`` with a hold at the end so every pass
gets a beat before the next one (``dsh-tool-row-sweep`` in
``ToolRow.module.css``). A terminal has no gradients, so the band becomes a
run of cells drawn in a brighter style — the geometry (band width, period,
easing, end hold) is dsh's, the material is ANSI.

Everything here is a **pure function of elapsed seconds**: no timers, no
threads, no state. The prompt redraw is the clock, and these only answer
"what does it look like at *t*?". That is what makes the motion
reproducible in a test and impossible to leak into the event loop — the
TUI's animation budget is one dictionary lookup per repaint.
"""

from __future__ import annotations

# Braille dots: narrow in every terminal (no East Asian width ambiguity),
# and the ten-frame cycle reads as rotation rather than as flicker.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_SECONDS = 0.09  # ~11 fps: smooth without asking for a redraw storm

# dsh's sweep timing, kept verbatim: a 2.6s cycle whose last 10% is a hold
# at the far edge.
SWEEP_SECONDS = 2.6
SWEEP_HOLD = 0.9  # the 90% keyframe: motion is done, the beat runs on
_MIN_BAND_CELLS = 3


def spinner_frame(elapsed: float) -> str:
    """The spinner glyph for ``elapsed`` seconds into a running activity."""
    if elapsed < 0:
        elapsed = 0.0
    index = int(elapsed / SPINNER_SECONDS) % len(SPINNER_FRAMES)
    return SPINNER_FRAMES[index]


def _ease_out(fraction: float) -> float:
    """CSS ``ease-out``, close enough for a band eight cells wide."""
    return 1.0 - (1.0 - fraction) ** 3


def sweep_span(width: int, elapsed: float) -> tuple[int, int]:
    """The ``[start, end)`` cell range the glare covers at ``elapsed``.

    Mirrors the keyframes: the band starts fully off the left edge, eases
    out to fully off the right edge by 90% of the cycle, and stays there
    for the remaining beat. An empty range means the glare is off-text,
    which is the correct answer during the hold.
    """
    if width <= 0:
        return (0, 0)
    band = max(_MIN_BAND_CELLS, width // 2)
    phase = (max(0.0, elapsed) % SWEEP_SECONDS) / SWEEP_SECONDS
    if phase >= SWEEP_HOLD:
        return (width, width)
    travelled = _ease_out(phase / SWEEP_HOLD) * (width + band)
    start = int(round(-band + travelled))
    return (max(0, start), max(0, min(width, start + band)))


def shimmer(
    text: str,
    elapsed: float,
    *,
    base_style: str,
    glare_style: str,
) -> list[tuple[str, str]]:
    """``text`` as formatted-text fragments with the sweep band brightened.

    The split is by characters, not cells: the label this decorates is the
    activity's own word ("Thinking", "Read"), which is ASCII by
    construction — the wide-glyph material in a status line is the detail
    tail, and that is not shimmered.
    """
    if not text:
        return []
    start, end = sweep_span(len(text), elapsed)
    if start >= end:
        return [(base_style, text)]
    fragments: list[tuple[str, str]] = []
    if start > 0:
        fragments.append((base_style, text[:start]))
    fragments.append((glare_style, text[start:end]))
    if end < len(text):
        fragments.append((base_style, text[end:]))
    return fragments
