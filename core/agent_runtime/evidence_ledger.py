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
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

DEFAULT_NO_PROGRESS_THRESHOLD = 3
# A long run can call with unbounded argument variety, and only recent evidence
# matters for spotting a stall — so the ledger keeps a bounded window of calls.
_MAX_TRACKED_CALLS = 256


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


class EvidenceLedger:
    """Counts how often each (canonical call, result) pair has been observed."""

    def __init__(self, threshold: int = DEFAULT_NO_PROGRESS_THRESHOLD) -> None:
        self.threshold = _validated(threshold)
        # call signature -> {result fingerprint: count}, kept in recency order
        # so the least recently used call is the one evicted at the bound.
        self._evidence: dict[tuple[str, str], dict[str, int]] = {}

    def observe(self, tool_name: str, arguments: Any, result: Any) -> str | None:
        """Record one finished call; return a reminder when it repeats evidence."""
        signature = (tool_name, _canonicalize(arguments))
        seen = self._evidence.pop(signature, None)
        if seen is None:
            seen = {}
        self._evidence[signature] = seen
        while len(self._evidence) > _MAX_TRACKED_CALLS:
            self._evidence.pop(next(iter(self._evidence)))
        fingerprint = _fingerprint(result)
        count = seen.get(fingerprint, 0) + 1
        seen[fingerprint] = count
        if count == self.threshold:
            return _no_progress_reminder(tool_name, count)
        return None


__all__ = ["DEFAULT_NO_PROGRESS_THRESHOLD", "EvidenceLedger"]
