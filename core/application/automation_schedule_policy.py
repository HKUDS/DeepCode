"""Pure scheduling policy for durable Automation occurrences.

The policy decides *when* an interval Automation is due and *what* to do with
an overlapping Run. It deliberately knows nothing about prompts, models,
projects, persistence, or execution. Those concerns remain with their owning
application services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from core.domain.common import require_aware


class AutomationMisfireStrategy(StrEnum):
    """How a scheduler handles interval deadlines missed between observations."""

    COALESCE = "coalesce"


class AutomationOverlapStrategy(StrEnum):
    """How a scheduler handles a due occurrence while a prior Run is open."""

    SKIP = "skip"


class AutomationOverlapDecision(StrEnum):
    """Concrete admission decision for the currently observed occurrence."""

    RUN = "run"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class IntervalScheduleAdvance:
    """One coalesced due window and its next cadence-aligned deadline."""

    scheduled_for: datetime
    observed_at: datetime
    next_run_at: datetime
    coalesced_occurrence_count: int

    def __post_init__(self) -> None:
        require_aware(self.scheduled_for, "scheduled_for")
        require_aware(self.observed_at, "observed_at")
        require_aware(self.next_run_at, "next_run_at")
        if self.coalesced_occurrence_count < 1:
            raise ValueError("coalesced_occurrence_count must be positive")
        if self.observed_at < self.scheduled_for:
            raise ValueError("observed_at cannot precede scheduled_for")
        if self.next_run_at <= self.observed_at:
            raise ValueError("next_run_at must be later than observed_at")


class AutomationSchedulePolicy(Protocol):
    """Injectable policy boundary consumed by ``AutomationService``."""

    @property
    def misfire_strategy(self) -> AutomationMisfireStrategy:
        """Return the strategy used for missed interval deadlines."""

    @property
    def overlap_strategy(self) -> AutomationOverlapStrategy:
        """Return the strategy used for overlapping Runs."""

    def initial_interval_deadline(
        self,
        *,
        activated_at: datetime,
        interval_seconds: int,
    ) -> datetime:
        """Return the first deadline after an interval schedule is activated."""

    def advance_due_interval(
        self,
        *,
        scheduled_for: datetime,
        observed_at: datetime,
        interval_seconds: int,
    ) -> IntervalScheduleAdvance:
        """Plan one due occurrence and the next future interval deadline."""

    def decide_overlap(
        self,
        *,
        has_open_run: bool,
    ) -> AutomationOverlapDecision:
        """Return whether the observed occurrence may start a new Run."""


@dataclass(frozen=True, slots=True)
class DefaultAutomationSchedulePolicy:
    """DeepCode V1 interval semantics.

    - Interval cadence remains anchored to the persisted nominal deadline.
    - Any number of missed deadlines coalesce into one observed occurrence.
    - A due occurrence is skipped when the previous Goal Run remains open.

    A future strategy can implement :class:`AutomationSchedulePolicy` without
    changing AutomationService or the Agent runtime.
    """

    @property
    def misfire_strategy(self) -> AutomationMisfireStrategy:
        return AutomationMisfireStrategy.COALESCE

    @property
    def overlap_strategy(self) -> AutomationOverlapStrategy:
        return AutomationOverlapStrategy.SKIP

    def initial_interval_deadline(
        self,
        *,
        activated_at: datetime,
        interval_seconds: int,
    ) -> datetime:
        require_aware(activated_at, "activated_at")
        interval = _interval(interval_seconds)
        return activated_at.astimezone(UTC) + interval

    def advance_due_interval(
        self,
        *,
        scheduled_for: datetime,
        observed_at: datetime,
        interval_seconds: int,
    ) -> IntervalScheduleAdvance:
        require_aware(scheduled_for, "scheduled_for")
        require_aware(observed_at, "observed_at")
        interval = _interval(interval_seconds)
        nominal = scheduled_for.astimezone(UTC)
        observed = observed_at.astimezone(UTC)
        if observed < nominal:
            raise ValueError("observed_at cannot precede scheduled_for")

        # Include the nominal deadline itself. Advancing by the number of due
        # ticks yields the first cadence-aligned deadline strictly in the
        # future, while representing all missed ticks with one occurrence.
        coalesced_count = (observed - nominal) // interval + 1
        return IntervalScheduleAdvance(
            scheduled_for=nominal,
            observed_at=observed,
            next_run_at=nominal + coalesced_count * interval,
            coalesced_occurrence_count=coalesced_count,
        )

    def decide_overlap(
        self,
        *,
        has_open_run: bool,
    ) -> AutomationOverlapDecision:
        return (
            AutomationOverlapDecision.SKIP
            if has_open_run
            else AutomationOverlapDecision.RUN
        )


def _interval(interval_seconds: int) -> timedelta:
    """Validate policy-level shape without duplicating product interval limits."""

    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or interval_seconds <= 0
    ):
        raise ValueError("interval_seconds must be a positive integer")
    return timedelta(seconds=interval_seconds)


__all__ = [
    "AutomationMisfireStrategy",
    "AutomationOverlapDecision",
    "AutomationOverlapStrategy",
    "AutomationSchedulePolicy",
    "DefaultAutomationSchedulePolicy",
    "IntervalScheduleAdvance",
]
