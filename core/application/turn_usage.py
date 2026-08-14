"""Provider-neutral Turn usage accounting over the durable event log."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from core.domain.event import DomainEvent

TURN_USAGE_EVENT_TYPE = "turn.usage.recorded"


def normalize_usage(value: object) -> dict[str, int]:
    """Keep only non-negative integer counters from a provider usage map."""

    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): counter
        for key, counter in value.items()
        if isinstance(counter, int) and not isinstance(counter, bool) and counter >= 0
    }


def add_usage(total: dict[str, int], addition: Mapping[str, int]) -> None:
    for key, value in addition.items():
        total[key] = total.get(key, 0) + value


def aggregate_recorded_usage(events: Iterable[DomainEvent]) -> dict[str, int]:
    """Rebuild one Turn's usage, ignoring duplicate response ordinals."""

    total: dict[str, int] = {}
    seen: set[int] = set()
    for event in events:
        if event.type != TURN_USAGE_EVENT_TYPE:
            continue
        ordinal = event.payload.get("responseOrdinal")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
            or ordinal in seen
        ):
            continue
        seen.add(ordinal)
        add_usage(total, normalize_usage(event.payload.get("usage")))
    return total
