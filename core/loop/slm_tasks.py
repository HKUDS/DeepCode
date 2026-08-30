"""P3-B (GenAI lesson 19): first consumer of the SLM subtask router.

P2-F1 (:mod:`core.loop.slm_routing`) introduced a pure decision mechanism —
``route_subtask(task_class, ...)`` — with no I/O and, until now, no caller.
Lesson 19 says high-frequency, low-complexity subtasks (tool-result cleanup,
summarization, classification) belong on the SLM tier. This module is the
first real consumer: it turns that decision into a preview-shaping policy
for oversized tool results.

Scope
-----
A true SLM *generation* call needs a separate provider channel, which the
runner does not carry. So the consumer here is deliberately decision-only
(zero network, zero async): when the router says the cleanup is an SLM-grade
task, the persisted-tool-result preview is shaped as a *clean*, dense digest
of the head of the payload (structured drop of noise, keeps signal); when it
is an LLM-grade task the raw truncated preview is kept as-is. Both shapes
stay side-effect free and cheap; wiring an actual SLM generation call into
this decision is left to a deployment that provides an SLM channel.

Enable: follows ``DEEPCODE_SLM_ROUTING`` (see :mod:`core.loop.slm_routing`).
"""

from __future__ import annotations

import re

from core.loop.slm_routing import (
    SUBTASK_MEDIUM,
    route_subtask,
    slm_routing_enabled,
)

# Maximum preview length after SLM-grade cleanup shaping.
_CLEAN_PREVIEW_CHARS = 600

# Lines that carry little decision signal in raw tool output (noise gutters).
_NOISE_LINE_PATTERN = re.compile(
    r"^\s*(?:ok|true|done|success|\[\s*\]|—+|-+|=+|\*+|#+|null|none)\s*$",
    re.IGNORECASE,
)


def should_route_tool_cleanup() -> bool:
    """Whether oversized tool-result cleanup should take the SLM-grade path.

    Uses the subtask router's decision for the ``medium`` class
    (summarization/cleanup). Unknown/disabled routing falls back to False so
    the caller keeps the default raw-truncation behavior.
    """
    if not slm_routing_enabled():
        return False
    decision = route_subtask(SUBTASK_MEDIUM)
    return decision.tier == "slm"


def shape_slm_preview(text: str, limit: int = _CLEAN_PREVIEW_CHARS) -> str:
    """Shrink ``text`` into a dense, noise-stripped preview.

    Decision-only shaping: drops blank/noise lines and returns the surviving
    head up to ``limit`` chars. Keeps JSON-ish payload structure by preserving
    first-non-blank lines rather than blind character truncation.
    """
    if not text:
        return text
    lines: list[str] = []
    for line in text.splitlines():
        if _NOISE_LINE_PATTERN.match(line):
            continue
        lines.append(line)
        if len("\n".join(lines)) >= limit:
            break
    preview = "\n".join(lines)[:limit]
    return preview if preview.strip() else text[:limit]


def choose_tool_result_preview(
    text: str,
    *,
    default_preview: str,
) -> str:
    """Pick the preview shape for a persisted oversized tool result.

    ``default_preview`` is the raw truncated head. When the SLM router says
    cleanup belongs on the SLM tier, returns the shaped dense preview instead
    (falls back to the default on any unexpected input).
    """
    if not isinstance(text, str) or not text:
        return default_preview
    try:
        if should_route_tool_cleanup():
            return shape_slm_preview(text)
    except Exception:  # never let routing errors break result handling
        return default_preview
    return default_preview


__all__ = [
    "choose_tool_result_preview",
    "shape_slm_preview",
    "should_route_tool_cleanup",
]
