"""Durable facts used to coordinate execution across application processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from core.domain.common import (
    new_id,
    require_aware,
    require_non_empty,
    require_prefixed_id,
    utc_now,
)


class ExecutionClass(StrEnum):
    """Persisted scheduling class; never infer it from a client surface."""

    INTERACTIVE = "interactive"
    MANUAL_AUTOMATION = "manual_automation"
    GOAL_CONTINUATION = "goal_continuation"
    EVENT_AUTOMATION = "event_automation"
    SCHEDULED_AUTOMATION = "scheduled_automation"
    BACKFILL = "backfill"


@dataclass(frozen=True, slots=True)
class RuntimeWorker:
    """One process-local execution host registered in the shared database."""

    pid: int
    surface: str
    id: str = field(default_factory=lambda: new_id("worker"))
    started_at: datetime = field(default_factory=utc_now)
    heartbeat_at: datetime = field(default_factory=utc_now)
    stopped_at: datetime | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.id, "worker")
        if isinstance(self.pid, bool) or self.pid < 1:
            raise ValueError("worker pid must be positive")
        require_non_empty(self.surface, "surface")
        require_aware(self.started_at, "started_at")
        require_aware(self.heartbeat_at, "heartbeat_at")
        if self.heartbeat_at < self.started_at:
            raise ValueError("worker heartbeat cannot precede its start")
        if self.stopped_at is not None:
            require_aware(self.stopped_at, "stopped_at")
            if self.stopped_at < self.heartbeat_at:
                raise ValueError("worker stop cannot precede its heartbeat")

    @property
    def active(self) -> bool:
        return self.stopped_at is None


@dataclass(frozen=True, slots=True)
class ResourceLease:
    """Latest fenced ownership state for one durable resource key."""

    resource_key: str
    epoch: int
    holder_worker_id: str | None
    holder_turn_id: str | None
    holder_turn_epoch: int | None
    acquired_at: datetime
    heartbeat_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.resource_key, "resource_key")
        if len(self.resource_key) > 512:
            raise ValueError("resource_key cannot exceed 512 characters")
        if isinstance(self.epoch, bool) or self.epoch < 1:
            raise ValueError("resource lease epoch must be positive")
        require_aware(self.acquired_at, "acquired_at")
        require_aware(self.heartbeat_at, "heartbeat_at")
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("resource heartbeat cannot precede acquisition")

        if self.holder_worker_id is None:
            if self.holder_turn_id is not None or self.holder_turn_epoch is not None:
                raise ValueError("a released lease cannot retain Turn ownership")
            if self.released_at is None or self.release_reason is None:
                raise ValueError("a released lease requires release facts")
            require_non_empty(self.release_reason, "release_reason")
            require_aware(self.released_at, "released_at")
            if self.released_at < self.heartbeat_at:
                raise ValueError("resource release cannot precede its heartbeat")
            return

        require_prefixed_id(self.holder_worker_id, "worker")
        if self.released_at is not None or self.release_reason is not None:
            raise ValueError("a held lease cannot carry release facts")
        if (self.holder_turn_id is None) != (self.holder_turn_epoch is None):
            raise ValueError("Turn resource ownership requires id and epoch")
        if self.holder_turn_id is not None:
            require_prefixed_id(self.holder_turn_id, "turn")
            if (
                isinstance(self.holder_turn_epoch, bool)
                or self.holder_turn_epoch is None
                or self.holder_turn_epoch < 1
            ):
                raise ValueError("holder_turn_epoch must be positive")

    @property
    def held(self) -> bool:
        return self.holder_worker_id is not None


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """The complete fencing receipt returned by one atomic claim."""

    worker_id: str
    leases: tuple[ResourceLease, ...]
    turn_id: str | None = None
    turn_epoch: int | None = None

    def __post_init__(self) -> None:
        require_prefixed_id(self.worker_id, "worker")
        if not self.leases:
            raise ValueError("a resource claim requires at least one lease")
        if (self.turn_id is None) != (self.turn_epoch is None):
            raise ValueError("Turn claims require id and epoch")
        if self.turn_id is not None:
            require_prefixed_id(self.turn_id, "turn")
            if (
                isinstance(self.turn_epoch, bool)
                or self.turn_epoch is None
                or self.turn_epoch < 1
            ):
                raise ValueError("turn_epoch must be positive")

        keys: set[str] = set()
        for lease in self.leases:
            if not lease.held:
                raise ValueError("a claim cannot contain a released lease")
            if lease.resource_key in keys:
                raise ValueError("a claim cannot repeat a resource key")
            keys.add(lease.resource_key)
            if lease.holder_worker_id != self.worker_id:
                raise ValueError("claim leases must have one worker owner")
            if lease.holder_turn_id != self.turn_id:
                raise ValueError("claim leases must have one Turn owner")
            if lease.holder_turn_epoch != self.turn_epoch:
                raise ValueError("claim leases must have one Turn epoch")

    @property
    def resource_keys(self) -> tuple[str, ...]:
        return tuple(lease.resource_key for lease in self.leases)


__all__ = [
    "ExecutionClass",
    "ResourceClaim",
    "ResourceLease",
    "RuntimeWorker",
]
