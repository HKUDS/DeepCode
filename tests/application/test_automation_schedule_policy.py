from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.application.automation_schedule_policy import (
    AutomationMisfireStrategy,
    AutomationOverlapDecision,
    AutomationOverlapStrategy,
    DefaultAutomationSchedulePolicy,
)


def test_initial_interval_deadline_is_one_interval_after_activation() -> None:
    policy = DefaultAutomationSchedulePolicy()
    activated_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    assert policy.initial_interval_deadline(
        activated_at=activated_at,
        interval_seconds=300,
    ) == datetime(2026, 7, 29, 12, 5, tzinfo=UTC)


def test_due_interval_advances_from_nominal_cadence_without_drift() -> None:
    policy = DefaultAutomationSchedulePolicy()
    scheduled_for = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    observed_at = scheduled_for + timedelta(minutes=5, seconds=30)

    advance = policy.advance_due_interval(
        scheduled_for=scheduled_for,
        observed_at=observed_at,
        interval_seconds=60,
    )

    assert advance.scheduled_for == scheduled_for
    assert advance.observed_at == observed_at
    assert advance.coalesced_occurrence_count == 6
    assert advance.next_run_at == scheduled_for + timedelta(minutes=6)
    assert advance.next_run_at != observed_at + timedelta(seconds=60)


@pytest.mark.parametrize(
    ("observed_offset", "expected_count", "next_offset"),
    [
        (timedelta(0), 1, timedelta(minutes=1)),
        (timedelta(seconds=59, microseconds=999_999), 1, timedelta(minutes=1)),
        (timedelta(minutes=1), 2, timedelta(minutes=2)),
    ],
)
def test_due_boundary_is_strictly_advanced_into_the_future(
    observed_offset: timedelta,
    expected_count: int,
    next_offset: timedelta,
) -> None:
    policy = DefaultAutomationSchedulePolicy()
    scheduled_for = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    advance = policy.advance_due_interval(
        scheduled_for=scheduled_for,
        observed_at=scheduled_for + observed_offset,
        interval_seconds=60,
    )

    assert advance.coalesced_occurrence_count == expected_count
    assert advance.next_run_at == scheduled_for + next_offset
    assert advance.next_run_at > advance.observed_at


def test_schedule_outputs_are_canonical_utc_instants() -> None:
    policy = DefaultAutomationSchedulePolicy()
    hong_kong = timezone(timedelta(hours=8))
    activated_at = datetime(2026, 7, 29, 20, 0, tzinfo=hong_kong)
    scheduled_for = datetime(2026, 7, 29, 20, 5, tzinfo=hong_kong)
    observed_at = datetime(2026, 7, 29, 20, 7, tzinfo=hong_kong)

    assert policy.initial_interval_deadline(
        activated_at=activated_at,
        interval_seconds=300,
    ) == datetime(2026, 7, 29, 12, 5, tzinfo=UTC)

    advance = policy.advance_due_interval(
        scheduled_for=scheduled_for,
        observed_at=observed_at,
        interval_seconds=300,
    )
    assert advance.scheduled_for == datetime(2026, 7, 29, 12, 5, tzinfo=UTC)
    assert advance.observed_at == datetime(2026, 7, 29, 12, 7, tzinfo=UTC)
    assert advance.next_run_at == datetime(2026, 7, 29, 12, 10, tzinfo=UTC)


def test_v1_policy_explicitly_coalesces_misfires_and_skips_overlap() -> None:
    policy = DefaultAutomationSchedulePolicy()

    assert policy.misfire_strategy is AutomationMisfireStrategy.COALESCE
    assert policy.overlap_strategy is AutomationOverlapStrategy.SKIP
    assert policy.decide_overlap(has_open_run=False) is AutomationOverlapDecision.RUN
    assert policy.decide_overlap(has_open_run=True) is AutomationOverlapDecision.SKIP


@pytest.mark.parametrize("interval_seconds", [True, False, 0, -1, 1.5, "60"])
def test_interval_must_be_a_positive_integer(interval_seconds: object) -> None:
    policy = DefaultAutomationSchedulePolicy()
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    with pytest.raises(
        ValueError,
        match="interval_seconds must be a positive integer",
    ):
        policy.initial_interval_deadline(
            activated_at=now,
            interval_seconds=interval_seconds,  # type: ignore[arg-type]
        )


def test_policy_rejects_naive_or_early_observations() -> None:
    policy = DefaultAutomationSchedulePolicy()
    aware = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)

    with pytest.raises(ValueError, match="activated_at must be timezone-aware"):
        policy.initial_interval_deadline(
            activated_at=naive,
            interval_seconds=60,
        )
    with pytest.raises(ValueError, match="scheduled_for must be timezone-aware"):
        policy.advance_due_interval(
            scheduled_for=naive,
            observed_at=aware,
            interval_seconds=60,
        )
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        policy.advance_due_interval(
            scheduled_for=aware,
            observed_at=naive,
            interval_seconds=60,
        )
    with pytest.raises(ValueError, match="cannot precede"):
        policy.advance_due_interval(
            scheduled_for=aware,
            observed_at=aware - timedelta(microseconds=1),
            interval_seconds=60,
        )
