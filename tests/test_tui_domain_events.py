from __future__ import annotations

from dataclasses import dataclass, field

from cli.tui.domain_events import DurableThreadEventCursor
from core.application.event_service import DeliveryBatch, EventBroker, ReplayPage
from core.domain.event import DomainEvent


@dataclass
class _DurableEvents:
    initial_head: int
    durable: list[DomainEvent] = field(default_factory=list)
    replay_limits: list[int] = field(default_factory=list)

    def head(self, thread_id: str) -> int:
        assert thread_id == "thread-events"
        return self.initial_head

    def replay_page(
        self,
        thread_id: str,
        *,
        after: int,
        limit: int,
    ) -> ReplayPage:
        assert thread_id == "thread-events"
        self.replay_limits.append(limit)
        candidates = [
            event
            for event in self.durable
            if event.thread_id == thread_id and event.sequence > after
        ]
        has_more = len(candidates) > limit
        events = tuple(candidates[:limit])
        return ReplayPage(
            events=events,
            next_after=events[-1].sequence if has_more and events else None,
            has_more=has_more,
        )


def _event(sequence: int, *, thread_id: str = "thread-events") -> DomainEvent:
    return DomainEvent(
        sequence=sequence,
        type="turn.item.added",
        thread_id=thread_id,
        payload={"sequence": sequence},
    )


def test_cursor_suppresses_history_but_closes_head_subscribe_gap() -> None:
    events = _DurableEvents(
        initial_head=2,
        durable=[_event(1), _event(2)],
    )
    cursor = DurableThreadEventCursor.at_head(events, "thread-events")
    race_event = _event(3)
    events.durable.append(race_event)
    delivered: list[DomainEvent] = []

    cursor.consume(DeliveryBatch((), 0), delivered.append)

    assert [event.sequence for event in delivered] == [3]
    assert cursor.sequence == 3


def test_cursor_recovers_broker_overflow_from_paged_durable_history() -> None:
    events = _DurableEvents(initial_head=0)
    cursor = DurableThreadEventCursor.at_head(events, "thread-events")
    broker = EventBroker(default_capacity=2)
    token = broker.subscribe()
    try:
        for sequence in range(1, 1_202):
            event = _event(sequence)
            events.durable.append(event)
            assert broker.publish(event)
        batch = broker.drain(token)
    finally:
        broker.unsubscribe(token)

    delivered: list[DomainEvent] = []
    cursor.consume(batch, delivered.append)

    assert batch.dropped == 1_199
    assert [event.sequence for event in delivered] == list(range(1, 1_202))
    assert events.replay_limits == [500, 500, 500]
    assert cursor.sequence == 1_201


def test_cursor_replays_a_live_gap_and_deduplicates_live_delivery() -> None:
    events = _DurableEvents(initial_head=0)
    cursor = DurableThreadEventCursor.at_head(events, "thread-events")
    delivered: list[DomainEvent] = []
    cursor.consume(DeliveryBatch((), 0), delivered.append)

    events.durable.extend([_event(1), _event(2), _event(3)])
    live = DeliveryBatch((_event(3),), 0)
    cursor.consume(live, delivered.append)
    cursor.consume(live, delivered.append)
    cursor.consume(
        DeliveryBatch((_event(1, thread_id="thread-other"),), 0),
        delivered.append,
    )

    assert [event.sequence for event in delivered] == [1, 2, 3]
    assert cursor.sequence == 3
