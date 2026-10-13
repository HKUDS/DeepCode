"""Text of items still streaming, newer than what the database holds.

A Turn's projection keeps streamed text here instead of rewriting the item
row for every batch. Each appended piece reaches clients as a live
``item.delta`` carrying UTF-8 byte offsets, the unit ``terminal.output`` uses,
and reads of an in-progress item show the text from here, so a client that
missed a piece catches up with one read.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from core.domain.item import Item, ItemStatus

#: The channel of an assistant message; reasoning uses its ReasoningChannel.
TEXT_CHANNEL = "text"


@dataclass(frozen=True, slots=True)
class LiveDelta:
    """One appended piece of an item's text, sent live and never stored."""

    thread_id: str
    turn_id: str
    item_id: str
    channel: str
    offset: int
    next_offset: int
    delta: str

    def view(self) -> dict[str, Any]:
        return {
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "itemId": self.item_id,
            "channel": self.channel,
            "offset": self.offset,
            "nextOffset": self.next_offset,
            "delta": self.delta,
        }


@dataclass(slots=True)
class _Streaming:
    thread_id: str
    turn_id: str
    summary: str
    payload: dict[str, Any]
    sent: dict[str, int]


class LiveItems:
    """Streaming items by id; safe to share across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _Streaming] = {}

    def begin(self, item: Item, texts: Mapping[str, str]) -> None:
        """Follow an item whose row already holds ``texts`` per channel."""

        sent = {channel: len(text.encode()) for channel, text in texts.items()}
        with self._lock:
            self._items[item.id] = _Streaming(
                item.thread_id, item.turn_id, item.summary, dict(item.payload), sent
            )

    def append(
        self,
        item_id: str,
        *,
        channel: str,
        delta: str,
        payload_update: Mapping[str, Any],
        summary: str | None = None,
    ) -> LiveDelta | None:
        """Record ``delta`` and the payload it results in; None if not followed."""

        with self._lock:
            streaming = self._items.get(item_id)
            if streaming is None:
                return None
            offset = streaming.sent.get(channel, 0)
            next_offset = offset + len(delta.encode())
            streaming.sent[channel] = next_offset
            streaming.payload = {**streaming.payload, **payload_update}
            if summary is not None:
                streaming.summary = summary
            return LiveDelta(
                streaming.thread_id,
                streaming.turn_id,
                item_id,
                channel,
                offset,
                next_offset,
                delta,
            )

    def overlay(self, item: Item) -> Item:
        """The item as clients see it: its streamed text, while in progress."""

        if item.status is not ItemStatus.IN_PROGRESS:
            return item
        with self._lock:
            streaming = self._items.get(item.id)
            if streaming is None:
                return item
            return replace(
                item, summary=streaming.summary, payload=dict(streaming.payload)
            )

    def finish(self, item_id: str) -> None:
        with self._lock:
            self._items.pop(item_id, None)

    def finish_turn(self, turn_id: str) -> None:
        with self._lock:
            for item_id in [
                item_id
                for item_id, streaming in self._items.items()
                if streaming.turn_id == turn_id
            ]:
                del self._items[item_id]


__all__ = ["TEXT_CHANNEL", "LiveDelta", "LiveItems"]
