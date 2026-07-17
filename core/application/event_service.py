"""Durable event replay plus bounded in-process fan-out."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass

from core.domain.event import DomainEvent
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository


@dataclass(frozen=True, slots=True)
class DeliveryBatch:
    events: tuple[DomainEvent, ...]
    dropped: int


@dataclass(frozen=True, slots=True)
class ReplayPage:
    events: tuple[DomainEvent, ...]
    next_after: int | None
    has_more: bool


@dataclass(slots=True)
class _Subscriber:
    queue: deque[DomainEvent]
    dropped: int = 0


class EventBroker:
    """Best-effort live delivery; durable replay remains authoritative."""

    def __init__(self, *, default_capacity: int = 256) -> None:
        if default_capacity < 1:
            raise ValueError("default_capacity must be positive")
        self.default_capacity = default_capacity
        self._condition = threading.Condition()
        self._subscribers: dict[str, _Subscriber] = {}

    def subscribe(self, *, capacity: int | None = None) -> str:
        size = capacity or self.default_capacity
        if size < 1:
            raise ValueError("capacity must be positive")
        token = uuid.uuid4().hex
        with self._condition:
            self._subscribers[token] = _Subscriber(deque(maxlen=size))
        return token

    def unsubscribe(self, token: str) -> None:
        with self._condition:
            self._subscribers.pop(token, None)
            self._condition.notify_all()

    def publish(self, event: DomainEvent) -> None:
        with self._condition:
            for subscriber in self._subscribers.values():
                if len(subscriber.queue) == subscriber.queue.maxlen:
                    subscriber.dropped += 1
                subscriber.queue.append(event)
            self._condition.notify_all()

    def drain(self, token: str) -> DeliveryBatch:
        with self._condition:
            return self._drain_locked(token)

    def wait_for_events(self, token: str, *, timeout: float) -> bool:
        """Wait for live work without removing it from the subscriber queue."""
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        with self._condition:
            subscriber = self._subscribers.get(token)
            if (
                subscriber is not None
                and not subscriber.queue
                and not subscriber.dropped
            ):
                self._condition.wait(timeout)
            subscriber = self._subscribers.get(token)
            return bool(
                subscriber is not None and (subscriber.queue or subscriber.dropped)
            )

    def _drain_locked(self, token: str) -> DeliveryBatch:
        subscriber = self._subscribers.get(token)
        if subscriber is None:
            return DeliveryBatch((), 0)
        batch = DeliveryBatch(tuple(subscriber.queue), subscriber.dropped)
        subscriber.queue.clear()
        subscriber.dropped = 0
        return batch


class EventService:
    def __init__(self, database: Database, broker: EventBroker) -> None:
        self.database = database
        self.broker = broker

    def replay(
        self, thread_id: str, *, after: int = 0, limit: int = 500
    ) -> list[DomainEvent]:
        return list(self.replay_page(thread_id, after=after, limit=limit).events)

    def replay_page(
        self, thread_id: str, *, after: int = 0, limit: int = 500
    ) -> ReplayPage:
        if after < 0:
            raise ValueError("after must not be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.read() as connection:
            events = EventRepository(connection).replay(
                thread_id, after=after, limit=limit + 1
            )
        has_more = len(events) > limit
        page = tuple(events[:limit])
        return ReplayPage(
            events=page,
            next_after=page[-1].sequence if has_more and page else None,
            has_more=has_more,
        )
