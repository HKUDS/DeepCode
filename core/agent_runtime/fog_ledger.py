"""Post-compaction fog observation (counts only, never decides).

Why this module exists
----------------------
LangWatch measured that within roughly 28 steps after a context compaction the
model's correction rate spikes — 41.9% against a 17.7% baseline (2.37x). A
compaction restores the context budget but not the working state: the agent
keeps re-deriving what the summary dropped. This module is the cheap half of
that finding. It *counts* the observation and appends it to an opt-in JSONL
ledger; it never chooses when to compact or what to keep, so it cannot move
any policy.

Design notes
------------
* Read-only with respect to compaction: the seam is the ``compaction.reset``
  key the retaining strategy already emits, read by key only — no import of
  the compactor, no mutation of history.
* Counts, not rates: each row stores the six raw counts so readers recompute
  ``fog_ratio``; a rounding change cannot make a persisted ratio drift from
  its own counts. ``None`` means "no samples", never ``inf``/``NaN``.
* Fail-open at the call site: a ledger write that raises is logged and
  swallowed by the runner, because a measurement must never abort the run it
  documents.
* stdlib only; no network, no model calls, no global state.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "FogLedger",
    "FogLedgerError",
    "FogObservation",
    "measure_post_compaction_fog",
]


class FogLedgerError(ValueError):
    """A fog observation, checkpoint, or ledger row is malformed."""


@dataclass(frozen=True, slots=True)
class FogObservation:
    """Error-step rates inside and outside the post-compaction fog window.

    LangWatch measured that within roughly 28 steps after a context compaction
    the correction rate spikes (41.9% vs a 17.7% baseline, 2.37x): the agent
    keeps re-deriving what the summary lost. This observation is the cheap
    half of that finding — it only counts; it never decides when to compact or
    what to keep, so it can sit beside ``M15`` without moving any policy.

    ``post_*`` counts steps inside the window after each compaction checkpoint;
    ``baseline_*`` counts every other step of the same history, which is the
    within-run comparison the ratio needs. ``None`` rates mean no samples, not
    zero error.
    """

    window_steps: int
    compactions: int
    post_steps: int
    post_error_steps: int
    baseline_steps: int
    baseline_error_steps: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.window_steps, int)
            or isinstance(self.window_steps, bool)
            or self.window_steps <= 0
        ):
            raise FogLedgerError(
                f"fog.window_steps must be a positive int, got {self.window_steps!r}"
            )
        for name in (
            "compactions",
            "post_steps",
            "post_error_steps",
            "baseline_steps",
            "baseline_error_steps",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise FogLedgerError(f"fog.{name} must be a non-negative int")
        if self.post_error_steps > self.post_steps:
            raise FogLedgerError("fog.post_error_steps cannot exceed fog.post_steps")
        if self.baseline_error_steps > self.baseline_steps:
            raise FogLedgerError(
                "fog.baseline_error_steps cannot exceed fog.baseline_steps"
            )

    @property
    def post_error_rate(self) -> float | None:
        """Error-step share inside the fog window; ``None`` with no samples."""
        return self.post_error_steps / self.post_steps if self.post_steps else None

    @property
    def baseline_error_rate(self) -> float | None:
        """Error-step share outside the window; ``None`` with no samples."""
        return (
            self.baseline_error_steps / self.baseline_steps
            if self.baseline_steps
            else None
        )

    @property
    def fog_ratio(self) -> float | None:
        """Post/baseline error rate — LangWatch's 2.37x finding is this number.

        ``None`` when either rate is undefined or the baseline rate is zero:
        a division claiming infinite fog would not be a measurement.
        """
        post = self.post_error_rate
        baseline = self.baseline_error_rate
        if post is None or baseline is None or baseline <= 0.0:
            return None
        return post / baseline

    def to_dict(self) -> dict[str, int]:
        """The six raw counts — derived rates are recomputed by readers.

        Storing only counts keeps the ledger replayable: a persisted ratio
        would drift from its own counts the moment rounding changed, while
        ``FogObservation(**payload)`` round-trips exactly.
        """
        return {
            "window_steps": self.window_steps,
            "compactions": self.compactions,
            "post_steps": self.post_steps,
            "post_error_steps": self.post_error_steps,
            "baseline_steps": self.baseline_steps,
            "baseline_error_steps": self.baseline_error_steps,
        }


def measure_post_compaction_fog(
    messages: Sequence[Mapping[str, Any]],
    *,
    window_steps: int = 28,
) -> FogObservation:
    """Count error steps in the ``window_steps`` after each compaction.

    The seam is the checkpoint message TailRetainingCompactionStrategy emits:
    a message carrying a ``compaction.reset`` key. It is read by key only, so
    this module keeps its no-imports-of-the-compactor boundary. A checkpoint
    also records ``retain``, the number of verbatim pre-compaction messages
    kept behind it; those are old work and are skipped before the window
    opens, otherwise a large retained tail would swallow the whole window.

    A step is one assistant turn plus the tool results that follow it; a step
    errs when any of those results is a ``str`` starting with ``"Error:"`` —
    the runner's uniform error rendering. Structured tool results (dicts,
    block lists) and empty-output placeholders are never counted as errors.
    Steps outside every window are the baseline the post-compaction rate is
    compared against.

    Read-only: the message list is never mutated and the compaction policy is
    never consulted.
    """
    if (
        not isinstance(window_steps, int)
        or isinstance(window_steps, bool)
        or window_steps <= 0
    ):
        raise FogLedgerError(
            f"fog window_steps must be a positive int, got {window_steps!r}"
        )

    steps: list[tuple[int, bool]] = []  # (first message index, error flag)
    checkpoints: list[tuple[int, int]] = []  # (message index, retained messages)
    open_step: int | None = None

    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise FogLedgerError(f"message {index} is not a mapping")

        compaction = message.get("compaction")
        if isinstance(compaction, Mapping) and compaction.get("reset"):
            retain = compaction.get("retain", 0)
            if not isinstance(retain, int) or isinstance(retain, bool) or retain < 0:
                raise FogLedgerError(
                    f"checkpoint at message {index} has a malformed retain count: "
                    f"{retain!r}"
                )
            checkpoints.append((index, retain))
            open_step = None
            continue

        role = message.get("role")
        if role == "assistant":
            steps.append((index, False))
            open_step = len(steps) - 1
        elif role == "tool" and open_step is not None:
            content = message.get("content")
            if isinstance(content, str) and content.startswith("Error:"):
                start, _ = steps[open_step]
                steps[open_step] = (start, True)

    post: set[int] = set()
    for checkpoint_index, retained in checkpoints:
        window_start = checkpoint_index + 1 + retained
        placed = 0
        for position, (start, _) in enumerate(steps):
            if start >= window_start:
                post.add(position)
                placed += 1
                if placed >= window_steps:
                    break

    post_steps = len(post)
    post_error_steps = sum(1 for position in post if steps[position][1])
    total_error_steps = sum(1 for _, errs in steps if errs)
    return FogObservation(
        window_steps=window_steps,
        compactions=len(checkpoints),
        post_steps=post_steps,
        post_error_steps=post_error_steps,
        baseline_steps=len(steps) - post_steps,
        baseline_error_steps=total_error_steps - post_error_steps,
    )


class FogLedger:
    """Append-only JSONL ledger of post-compaction fog observations.

    One JSON object per line on its own path — deliberately separate from the
    paired harness-measurement ledger: a fog row is six raw counts, not a
    paired record, and the two schemas must never share a file. Same write
    discipline (O_APPEND + fsync) so concurrent writers cannot interleave a
    partial line. No ``read_all``: consumers read the JSONL directly, which
    keeps this class a pure write side (E1).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def append(self, observation: FogObservation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(observation.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        )
        # O_APPEND keeps concurrent writers from interleaving a partial line.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
