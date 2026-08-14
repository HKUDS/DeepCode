"""Lossless CLI consumption over the durable Thread event stream."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.application.event_service import DeliveryBatch, EventService
from core.domain.event import DomainEvent


_REPLAY_PAGE_SIZE = 500


@dataclass(slots=True)
class DurableThreadEventCursor:
    """Recover broker overflow and sequence gaps from ``event_log``.

    Constructing at the current head suppresses historical events. The first
    consume still performs a replay, closing the unavoidable head/subscribe
    race without depending on the best-effort broker.
    """

    events: EventService
    thread_id: str
    sequence: int
    _initial_replay_required: bool = True

    @classmethod
    def at_head(
        cls,
        events: EventService,
        thread_id: str,
    ) -> DurableThreadEventCursor:
        return cls(
            events=events,
            thread_id=thread_id,
            sequence=events.head(thread_id),
        )

    def consume(
        self,
        batch: DeliveryBatch,
        sink: Callable[[DomainEvent], None],
    ) -> None:
        if self._initial_replay_required or batch.dropped:
            self._replay(sink)
            self._initial_replay_required = False

        for event in batch.events:
            if event.thread_id != self.thread_id or event.sequence <= self.sequence:
                continue
            if event.sequence > self.sequence + 1:
                self._replay(sink)
            if event.sequence <= self.sequence:
                continue
            sink(event)
            self.sequence = event.sequence

    def _replay(self, sink: Callable[[DomainEvent], None]) -> None:
        while True:
            page = self.events.replay_page(
                self.thread_id,
                after=self.sequence,
                limit=_REPLAY_PAGE_SIZE,
            )
            for event in page.events:
                if event.sequence <= self.sequence:
                    continue
                sink(event)
                self.sequence = event.sequence
            if not page.has_more:
                return


__all__ = ["DurableThreadEventCursor"]
