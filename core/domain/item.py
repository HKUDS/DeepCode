"""Stable timeline item model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    JsonObject,
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
    validate_json_object,
)


class ItemKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    REASONING_SUMMARY = "reasoning_summary"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    COMMAND_EXECUTION = "command_execution"
    FILE_CHANGE = "file_change"
    DIFF = "diff"
    TEST_RESULT = "test_result"
    APPROVAL_REQUEST = "approval_request"
    WORKFLOW_STAGE = "workflow_stage"
    ARTIFACT = "artifact"
    ERROR = "error"
    COMPLETION = "completion"


class ItemStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class Item:
    thread_id: str
    turn_id: str
    ordinal: int
    kind: ItemKind
    status: ItemStatus
    summary: str
    payload: JsonObject = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("item"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "item")
        require_non_empty(self.thread_id, "thread_id")
        require_prefixed_id(self.turn_id, "turn")
        if self.ordinal < 1:
            raise ValueError("ordinal must be positive")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        validate_json_object(self.payload, "payload")
