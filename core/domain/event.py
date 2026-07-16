"""Authoritative event-log record for UI replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.domain.common import (
    JsonObject,
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
    validate_json_object,
)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    sequence: int
    type: str
    thread_id: str
    payload: JsonObject
    turn_id: str | None = None
    item_id: str | None = None
    id: str = field(default_factory=lambda: new_id("evt"))
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "evt")
        require_non_empty(self.thread_id, "thread_id")
        if self.turn_id is not None:
            require_prefixed_id(self.turn_id, "turn")
        if self.item_id is not None:
            require_prefixed_id(self.item_id, "item")
            if self.turn_id is None:
                raise ValueError("item events require turn_id")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")
        require_non_empty(self.type, "type")
        require_aware(self.timestamp, "timestamp")
        validate_json_object(self.payload, "payload")
