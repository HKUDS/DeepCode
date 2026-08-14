"""Durable event replay plus bounded in-process fan-out."""

from __future__ import annotations

import logging
import math
import threading
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from core.domain.event import DomainEvent
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository


logger = logging.getLogger(__name__)
DEFAULT_RELAY_POLL_INTERVAL = 0.1
DEFAULT_RELAY_BATCH_SIZE = 500


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


@dataclass(slots=True)
class _SequenceWatermark:
    contiguous: int = 0
    out_of_order: set[int] = field(default_factory=set)

    def accept(self, sequence: int) -> bool:
        if sequence <= self.contiguous or sequence in self.out_of_order:
            return False
        self.out_of_order.add(sequence)
        self._advance()
        return True

    def seed(self, sequence: int) -> None:
        if sequence > self.contiguous:
            self.contiguous = sequence
        self.out_of_order = {
            candidate for candidate in self.out_of_order if candidate > self.contiguous
        }
        self._advance()

    def _advance(self) -> None:
        next_sequence = self.contiguous + 1
        while next_sequence in self.out_of_order:
            self.out_of_order.remove(next_sequence)
            self.contiguous = next_sequence
            next_sequence += 1


class EventBroker:
    """Best-effort live delivery; durable replay remains authoritative."""

    def __init__(self, *, default_capacity: int = 256) -> None:
        if default_capacity < 1:
            raise ValueError("default_capacity must be positive")
        self.default_capacity = default_capacity
        self._condition = threading.Condition()
        self._subscribers: dict[str, _Subscriber] = {}
        self._watermarks: dict[str, _SequenceWatermark] = {}

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

    def seed_sequences(self, heads: Mapping[str, int]) -> None:
        """Mark pre-existing durable history without delivering it live."""

        with self._condition:
            for thread_id, sequence in heads.items():
                if sequence < 0:
                    raise ValueError("sequence heads cannot be negative")
                watermark = self._watermarks.setdefault(
                    thread_id,
                    _SequenceWatermark(),
                )
                watermark.seed(sequence)

    def publish(self, event: DomainEvent) -> bool:
        """Deliver an event at most once for its authoritative stream sequence."""

        with self._condition:
            watermark = self._watermarks.setdefault(
                event.thread_id,
                _SequenceWatermark(),
            )
            if not watermark.accept(event.sequence):
                return False
            for subscriber in self._subscribers.values():
                if len(subscriber.queue) == subscriber.queue.maxlen:
                    subscriber.dropped += 1
                subscriber.queue.append(event)
            self._condition.notify_all()
            return True

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

    def head(self, thread_id: str) -> int:
        """Return the latest committed sequence for one Thread event stream."""

        with self.database.read() as connection:
            return EventRepository(connection).sequence_head(thread_id)

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


class DurableEventRelay:
    """Relay committed events from other processes into one local broker.

    Per-thread relay cursors advance only from rows actually read from
    ``event_log``. Broker delivery watermarks are deliberately separate so a
    newer local publication cannot make the relay skip an older external row.
    """

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        *,
        poll_interval: float = DEFAULT_RELAY_POLL_INTERVAL,
        batch_size: int = DEFAULT_RELAY_BATCH_SIZE,
    ) -> None:
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(poll_interval)
            or poll_interval <= 0
        ):
            raise ValueError("poll_interval must be a finite positive number")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise ValueError("batch_size must be a positive integer")
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.database = database
        self.broker = broker
        self.poll_interval = float(poll_interval)
        self.batch_size = batch_size
        self._cursors: dict[str, int] = {}
        self._lifecycle_lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._initialized = False
        self._closed = False

    @property
    def active(self) -> bool:
        with self._lifecycle_lock:
            return bool(
                self._thread is not None
                and self._thread.is_alive()
                and not self._stop.is_set()
            )

    def start(self, *, background: bool = True) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("a closed durable event relay cannot restart")
            self._initialize_locked()
            if not background:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deepcode-durable-event-relay",
                daemon=True,
            )
            self._thread.start()

    def poll_once(self) -> int:
        """Publish at most one configured page per changed event stream."""

        with self._lifecycle_lock:
            if self._closed:
                return 0
            self._initialize_locked()
        delivered = 0
        with self._poll_lock:
            with self.database.read() as connection:
                repository = EventRepository(connection)
                heads = repository.sequence_heads()
                for thread_id in sorted(heads):
                    after = self._cursors.get(thread_id, 0)
                    if heads[thread_id] <= after:
                        continue
                    events = repository.replay(
                        thread_id,
                        after=after,
                        limit=self.batch_size,
                    )
                    for event in events:
                        if self.broker.publish(event):
                            delivered += 1
                    if events:
                        self._cursors[thread_id] = events[-1].sequence
        return delivered

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _initialize_locked(self) -> None:
        if self._initialized:
            return
        with self.database.read() as connection:
            heads = EventRepository(connection).sequence_heads()
        self._cursors = dict(heads)
        self.broker.seed_sequences(heads)
        self._initialized = True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - a transient read must not kill relay
                logger.exception("durable event relay poll failed")
            self._stop.wait(self.poll_interval)
