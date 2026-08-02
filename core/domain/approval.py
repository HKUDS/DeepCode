"""Approval records enforced by the backend."""

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


class ApprovalCategory(StrEnum):
    COMMAND = "command"
    FILE_WRITE = "file_write"
    NETWORK = "network"
    EXTERNAL_TOOL = "external_tool"
    DESTRUCTIVE = "destructive"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED_ONCE = "approved_once"
    APPROVED_SESSION = "approved_session"
    DENIED = "denied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def is_resolved(self) -> bool:
        return self is not ApprovalStatus.PENDING


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """A durable Thread-scoped grant for one exact tool capability.

    The permission engine still evaluates every call before the approval
    callback runs.  This record only remembers a user's ``approved_session``
    response for the same canonical Thread and tool; it cannot widen policy,
    bypass a deny, or authorize a different tool.
    """

    thread_id: str
    tool_name: str
    source_approval_id: str
    granted_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_non_empty(self.thread_id, "thread_id")
        require_non_empty(self.tool_name, "tool_name")
        require_prefixed_id(self.source_approval_id, "apr")
        require_aware(self.granted_at, "granted_at")


@dataclass(frozen=True, slots=True)
class Approval:
    thread_id: str
    turn_id: str
    item_id: str
    category: ApprovalCategory
    request: JsonObject
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision: JsonObject | None = None
    id: str = field(default_factory=lambda: new_id("apr"))
    requested_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "apr")
        require_non_empty(self.thread_id, "thread_id")
        require_prefixed_id(self.turn_id, "turn")
        require_prefixed_id(self.item_id, "item")
        require_aware(self.requested_at, "requested_at")
        validate_json_object(self.request, "request")
        if self.decision is not None:
            validate_json_object(self.decision, "decision")
        if self.status.is_resolved:
            if self.resolved_at is None or self.decision is None:
                raise ValueError("resolved approvals require decision and resolved_at")
            require_aware(self.resolved_at, "resolved_at")
        elif self.resolved_at is not None or self.decision is not None:
            raise ValueError("pending approvals cannot have a decision")
