"""Advisory no-progress reminders — the evidence half of loop-breaking.

``repeat_guard`` watches *call* repetition: identical consecutive calls earn an
escalating reminder. That misses the other classic loop, where the model
interleaves different calls (A, B, A, B, ...) and every attempt hands back the
same evidence it already had — no call is ever repeated consecutively, yet the
run is learning nothing.

``EvidenceLedger`` keys on the call *and its result*: for each canonical call
signature it counts the result fingerprints already seen. When one pair comes
back ``threshold`` times the call provably is not making progress, and a
reminder is emitted.

The fingerprint is the normalized tool result — the exact content the model
read, truncation included. Two results the model cannot tell apart are the same
evidence, which is the property that matters here.

Same discipline as ``repeat_guard``: advisory only, it never delays, rewrites,
or blocks a call, the decision stays entirely with the model, and it is the
FIRST line of defense in front of any hard stop (``max_iterations``,
``should_stop_callback``). The reminder never quotes tool output: results are
unbounded and may be attacker-controlled, so they stay out of the prompt. The
runner injects these through its existing reminder channel, skipping any call
``repeat_guard`` already flagged, so one iteration injects at most one reminder
per call.

Reminders alone are not enough: a model can ignore them indefinitely. In one
2026-11 Goal run the model alternated two commands 741 times in a single Turn,
getting the same two results each time, until the provider account ran dry.
So the ledger also tracks two streaks and, past a limit, tells the runner to
stop (``stalled``), with one warning first:

- results in a row the model had already seen for that call (no new evidence);
- calls in a row it had already made earlier in the run (no new action). This
  catches loops whose results differ only by noise such as test timings, so
  its limit is higher: polling a running job legitimately repeats a call.

Any new result resets the first streak; any new call resets both. Some calls
leave both alone:

- a call to a tool that waits on running work elsewhere (``Tool.waits``): its
  result is meant to repeat while that work runs;
- the first call again of something the model made before the history was
  summarized (``out_of_view``): it no longer has that result in view. Nothing
  is reset, so a loop that keeps filling the context still reaches the stop.

The stop comes only after the model has had one step to read the warning.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

DEFAULT_NO_PROGRESS_THRESHOLD = 3
# A long run can call with unbounded argument variety, and only recent evidence
# matters for spotting a stall — so the ledger keeps a bounded window of calls.
_MAX_TRACKED_CALLS = 256
# Streak lengths that earn a warning, then stop the run.
STALE_RESULT_WARNING = 8
STALE_RESULT_STOP = 12
REPEATED_CALL_WARNING = 30
REPEATED_CALL_STOP = 40


def _canonicalize(value: Any) -> str:
    """Order must not defeat detection (same key as ``repeat_guard``)."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _fingerprint(result: Any) -> str:
    """Fingerprint what the model read, whatever shape the tool handed back.

    Results cross a dynamic boundary: most tools return text, but a tool is free
    to return structured content (the Goal tools return dicts) and ``content``
    carries it through untouched. A non-text result therefore gets the same
    canonical form as a call signature — the run must never fail just because a
    reminder could not be computed.
    """
    text = result if isinstance(result, str) else _canonicalize(result)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _validated(threshold: int) -> int:
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 2:
        raise ValueError("no-progress threshold must be an int >= 2")
    return threshold


def _no_progress_reminder(tool_name: str, count: int) -> str:
    return (
        f"Reminder: `{tool_name}` has now returned the same result {count} times "
        "in this run, counting attempts separated by other calls. Repeating it "
        "is unlikely to produce new evidence — change the approach instead: vary "
        "the arguments, use a different tool, or say what you are blocked on."
    )


def _stall_warning(count: int, what: str) -> str:
    return (
        f"Warning: your last {count} tool calls {what}. This run is not making "
        "progress. If the next few calls bring nothing new, the run will be "
        "stopped. Change the approach now, or stop and say what you are "
        "blocked on."
    )


class EvidenceLedger:
    """Counts how often each (canonical call, result) pair has been observed."""

    def __init__(
        self,
        threshold: int = DEFAULT_NO_PROGRESS_THRESHOLD,
        *,
        stop: bool = True,
    ) -> None:
        self.threshold = _validated(threshold)
        self._stop = stop
        # call signature -> {result fingerprint: count}, kept in recency order
        # so the least recently used call is the one evicted at the bound.
        self._evidence: dict[tuple[str, str], dict[str, int]] = {}
        self._stale_results = 0
        self._repeated_calls = 0
        self._warning: str | None = None
        # A warning handed out since the last observed call: the model has
        # not had a step to act on it yet.
        self._warning_unread = False
        # Calls whose results left the model's view in a summary.
        self._out_of_view: set[tuple[str, str]] = set()

    def observe(
        self,
        tool_name: str,
        arguments: Any,
        result: Any,
        *,
        waits: bool = False,
    ) -> str | None:
        """Record one finished call; return a reminder when it repeats evidence.

        ``waits`` marks a tool that waits on work elsewhere: its evidence is
        recorded but it does not move the stall streaks.
        """
        self._warning_unread = False
        signature = (tool_name, _canonicalize(arguments))
        if signature in self._out_of_view:
            self._out_of_view.discard(signature)
            waits = True  # reading it again is not a stall
        seen = self._evidence.pop(signature, None)
        repeated_call = seen is not None
        if seen is None:
            seen = {}
        self._evidence[signature] = seen
        while len(self._evidence) > _MAX_TRACKED_CALLS:
            self._evidence.pop(next(iter(self._evidence)))
        fingerprint = _fingerprint(result)
        count = seen.get(fingerprint, 0) + 1
        seen[fingerprint] = count
        if not waits:
            self._track_streaks(repeated_call=repeated_call, stale_result=count > 1)
        if count == self.threshold:
            return _no_progress_reminder(tool_name, count)
        return None

    def _track_streaks(self, *, repeated_call: bool, stale_result: bool) -> None:
        self._repeated_calls = self._repeated_calls + 1 if repeated_call else 0
        self._stale_results = self._stale_results + 1 if stale_result else 0
        if not self._stop:
            return
        # Each streak grows by one, so equality warns once per streak.
        if self._stale_results == STALE_RESULT_WARNING:
            self._warning = _stall_warning(
                self._stale_results,
                "only returned results you had already seen in this run",
            )
        elif self._repeated_calls == REPEATED_CALL_WARNING:
            self._warning = _stall_warning(
                self._repeated_calls,
                "only repeated calls you had already made in this run",
            )

    def take_warning(self) -> str | None:
        """The pending stall warning, once."""
        warning, self._warning = self._warning, None
        if warning is not None:
            self._warning_unread = True
        return warning

    @property
    def stalled(self) -> str | None:
        """Why the run should stop now, or None while it may go on."""
        if not self._stop or self._warning_unread:
            return None
        if self._stale_results >= STALE_RESULT_STOP:
            return (
                f"my last {self._stale_results} tool calls only returned "
                "results I had already seen"
            )
        if self._repeated_calls >= REPEATED_CALL_STOP:
            return (
                f"my last {self._repeated_calls} tool calls only repeated "
                "calls I had already made"
            )
        return None

    def reset_streaks(self) -> None:
        """Start over, for example after the user added new instructions."""
        self._stale_results = 0
        self._repeated_calls = 0
        self._warning = None
        self._warning_unread = False

    def out_of_view(self) -> None:
        """The history was summarized: earlier results left the model's view.

        The next call of each earlier signature counts as neither progress
        nor a stall; later ones count as usual. The streaks stay, so frequent
        compaction cannot hide a loop.
        """
        self._out_of_view = set(self._evidence)


__all__ = [
    "DEFAULT_NO_PROGRESS_THRESHOLD",
    "REPEATED_CALL_STOP",
    "REPEATED_CALL_WARNING",
    "STALE_RESULT_STOP",
    "STALE_RESULT_WARNING",
    "EvidenceLedger",
]
