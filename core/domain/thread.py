"""Thread model: one durable stream of work inside a project."""

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
from core.domain.execution_security import ExecutionAccessPreset


class ThreadMode(StrEnum):
    CODE = "code"
    PAPER = "paper"
    BRIEF = "brief"
    REVIEW = "review"
    GOAL = "goal"


class ThreadStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Thread:
    project_id: str
    title: str
    mode: ThreadMode
    workspace_path: str
    status: ThreadStatus = ThreadStatus.IDLE
    model: str | None = None
    connection_id: str | None = None
    reasoning_effort: str | None = None
    access_preset_override: ExecutionAccessPreset | None = None
    parent_thread_id: str | None = None
    worktree_path: str | None = None
    id: str = field(default_factory=lambda: new_id("thr"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        # Thread identity is the canonical SessionStore id. Existing sessions
        # use short hex ids; early Desktop projections used ``thr_`` ids.
        require_non_empty(self.id, "id")
        require_prefixed_id(self.project_id, "proj")
        if self.parent_thread_id is not None:
            require_non_empty(self.parent_thread_id, "parent_thread_id")
        require_non_empty(self.title, "title")
        require_non_empty(self.workspace_path, "workspace_path")
        if self.connection_id is not None:
            require_non_empty(self.connection_id, "connection_id")
        if self.reasoning_effort is not None:
            require_non_empty(self.reasoning_effort, "reasoning_effort")
        if self.access_preset_override is not None and not isinstance(
            self.access_preset_override,
            ExecutionAccessPreset,
        ):
            raise TypeError(
                "access_preset_override must be an ExecutionAccessPreset or None"
            )
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            require_aware(self.archived_at, "archived_at")
        if self.status is ThreadStatus.ARCHIVED and self.archived_at is None:
            raise ValueError("archived threads require archived_at")
        if self.status is not ThreadStatus.ARCHIVED and self.archived_at is not None:
            raise ValueError("only archived threads may have archived_at")
