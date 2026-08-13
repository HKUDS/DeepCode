"""Advisory repeat-call reminders — a soft loop-breaker, not a circuit breaker.

Borrowed from dsh's ``repeat-tool-reminder`` guard. The tracker watches the
stream of tool calls for runs of consecutive calls to the same tool with
identical canonicalized arguments, and at configured run lengths returns an
escalating reminder for the runner to inject as a user-visible message. It
never delays, rewrites, or blocks a call: a legitimately repeated call is
slowed by nothing, and the decision — retry differently, gather more
evidence, or conclude — stays entirely with the model.

This is deliberately the FIRST line of defense, in front of any hard stop
(``max_iterations``, ``should_stop_callback``): a model that loops usually
recovers when told exactly what it is repeating, and killing the run throws
away all of its accumulated context.

Denied and failed calls count too — a model hammering a call the permission
gate keeps rejecting is exactly the loop worth interrupting.
"""

from __future__ import annotations

import json
from typing import Any

# Run lengths that trigger a reminder. The first threshold gets the gentle
# nudge, later ones the detailed version naming the tool and its arguments —
# keyed to position, not to the literal count, so custom thresholds keep the
# gentle-then-detailed escalation.
DEFAULT_THRESHOLDS: tuple[int, ...] = (3, 5, 8)

# Cap on the canonical-arguments quote inside the DETAILED reminder. Bounds
# only the model-visible text; the chain key always uses the full canonical
# string, so oversized arguments cannot evade detection by exceeding the cap.
_ARGUMENTS_PREVIEW_CHARS = 300

_GENTLE_REMINDER = (
    "You are repeating the exact same tool call with identical arguments. "
    "Carefully analyze the previous result before calling again: if the task "
    "is not complete, try a different approach or different arguments instead "
    "of repeating the call."
)


def _detailed_reminder(tool_name: str, count: int, preview: str) -> str:
    return (
        "Repeated tool call detected:\n"
        f"- tool: {tool_name}\n"
        f"- consecutive_calls: {count}\n"
        f"- arguments: {preview}\n"
        "The repeated calls are not making progress. Do not call this tool "
        "with these exact arguments again. Inspect the latest result and "
        "choose a different action, different arguments, or finish the task "
        "if enough evidence has been gathered."
    )


def _canonicalize(arguments: Any) -> str:
    """Order-independent canonical form of a call's arguments.

    ``sort_keys`` sorts recursively, so two argument objects differing only in
    property order canonicalize identically; ``default=str`` keeps the tracker
    total over the odd non-JSON value a tool cast may have produced.
    """
    try:
        return json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(arguments)


def _validated(thresholds: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    """Fail-loud validation, normalized ascending (the escalation rule reads
    the first element as the gentle tier)."""
    values = tuple(thresholds)
    if not values:
        raise ValueError("repeat-call thresholds must not be empty")
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            raise ValueError(
                f"invalid repeat-call threshold {value!r} — every threshold "
                "must be an integer >= 2"
            )
    if len(set(values)) != len(values):
        raise ValueError("repeat-call thresholds must not contain duplicates")
    return tuple(sorted(values))


class RepeatCallTracker:
    """One run's consecutive-repeat chain and its escalation state."""

    def __init__(
        self,
        thresholds: tuple[int, ...] | list[int] = DEFAULT_THRESHOLDS,
        *,
        arguments_preview_chars: int = _ARGUMENTS_PREVIEW_CHARS,
    ) -> None:
        if arguments_preview_chars < 1:
            raise ValueError(
                f"invalid arguments_preview_chars {arguments_preview_chars} — "
                "must be an integer >= 1"
            )
        self._thresholds = _validated(thresholds)
        self._threshold_set = frozenset(self._thresholds)
        self._preview_chars = arguments_preview_chars
        self._key: tuple[str, str] | None = None
        self._count = 0

    def observe(self, tool_name: str, arguments: Any) -> str | None:
        """Advance the chain for one call; return the reminder it earned, if any.

        A different call (name or canonical arguments) restarts the chain at
        one, so only genuinely consecutive repeats escalate.
        """
        canonical = _canonicalize(arguments)
        key = (tool_name, canonical)
        self._count = self._count + 1 if key == self._key else 1
        self._key = key
        if self._count not in self._threshold_set:
            return None
        if self._count == self._thresholds[0]:
            return _GENTLE_REMINDER
        return _detailed_reminder(tool_name, self._count, self._preview(canonical))

    def _preview(self, canonical: str) -> str:
        if len(canonical) <= self._preview_chars:
            return canonical
        omitted = len(canonical) - self._preview_chars
        return f"{canonical[: self._preview_chars]}… (+{omitted} more chars)"


__all__ = ["DEFAULT_THRESHOLDS", "RepeatCallTracker"]
