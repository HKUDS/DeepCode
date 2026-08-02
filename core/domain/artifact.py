"""Artifact metadata; large data remains outside the event stream."""

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
class Artifact:
    thread_id: str
    kind: str
    name: str
    media_type: str
    storage_path: str
    turn_id: str | None = None
    workflow_run_id: str | None = None
    byte_size: int | None = None
    metadata: JsonObject = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("art"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "art")
        require_non_empty(self.thread_id, "thread_id")
        if self.turn_id is not None:
            require_prefixed_id(self.turn_id, "turn")
        if self.workflow_run_id is not None:
            require_prefixed_id(self.workflow_run_id, "wfr")
        for value, name in (
            (self.kind, "kind"),
            (self.name, "name"),
            (self.media_type, "media_type"),
            (self.storage_path, "storage_path"),
        ):
            require_non_empty(value, name)
        if self.byte_size is not None and self.byte_size < 0:
            raise ValueError("byte_size cannot be negative")
        require_aware(self.created_at, "created_at")
        validate_json_object(self.metadata, "metadata")
