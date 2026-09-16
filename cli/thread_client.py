"""The command boundary consumed by TUI presentation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from core.domain.approval import Approval, ApprovalStatus
from core.domain.event import DomainEvent
from core.domain.execution_profile import ExecutionProfile
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.project import Project
from core.domain.thread import Thread
from core.domain.turn import Turn
from core.sessions import SessionStore


@dataclass(frozen=True, slots=True)
class HeadlessTurnOptions:
    prompt: str
    workspace: str | None = None
    resume_id: str | None = None
    connection_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    skill_identifiers: tuple[str, ...] = ()
    max_iterations: int | None = None
    trust_workspace: bool = False
    access_preset: ExecutionAccessPreset | None = None
    agent_preset: str | None = None


@dataclass(frozen=True, slots=True)
class HeadlessTurnResult:
    turn: Turn
    session_id: str
    workspace: str


@dataclass(frozen=True, slots=True)
class TurnDelivery:
    kind: str
    turn: Turn


@dataclass(frozen=True, slots=True)
class ThreadListing:
    """One row of the resume picker — display data only."""

    session_id: str
    title: str
    message_count: int
    updated_at: datetime
    workspace: str
    is_current: bool


def turn_access_summary(turn: Turn) -> str:
    profile = turn.execution_security_profile
    if profile is not None:
        if profile.access_preset is not None:
            return profile.access_preset.value.replace("_", " ")
        sandbox = "sandboxed" if profile.command_sandbox else "unsandboxed"
        return f"legacy {profile.permission_mode.value.replace('_', ' ')} · {sandbox}"
    if turn.execution_permission_mode is not None:
        return f"legacy {turn.execution_permission_mode.value.replace('_', ' ')}"
    return "legacy unknown"


class ThreadClient(Protocol):
    """Presentation consumes commands and snapshots, never an execution owner."""

    runtime_mode: str
    workspace: str
    thread: Thread
    project: Project
    execution_profile: ExecutionProfile
    store: SessionStore
    llm: Any
    skills: Any
    plugins: Any
    mcp: Any
    goals: Any

    @property
    def session_id(self) -> str: ...
    @property
    def project_trusted(self) -> bool: ...
    @property
    def access_preset_override(self) -> ExecutionAccessPreset | None: ...
    def access_summary(self) -> str: ...
    def frozen_access_summaries(self) -> tuple[str | None, tuple[str, ...]]: ...
    def send(self, prompt: str, *, skill_ids: tuple[str, ...] = ()) -> Any: ...
    def queue(self, prompt: str, *, skill_ids: tuple[str, ...] = ()) -> Any: ...
    def has_active_turn(self) -> bool: ...
    def rename_thread(self, title: str) -> Thread: ...
    def delete_session(self, session_id: str) -> None: ...
    def last_terminal_turn(self) -> Turn | None: ...
    def retry_turn(self, turn_id: str) -> Turn: ...
    def interrupt(self) -> tuple[bool, Turn] | None: ...
    def pending_approval(self) -> Approval | None: ...
    def respond_to_approval(
        self, approval_id: str, decision: ApprovalStatus
    ) -> Approval: ...
    async def wait_until_idle(self) -> None: ...
    def new_thread(self, *, title: str = "") -> Thread: ...
    def resume(self, session_id: str) -> Thread: ...
    def list_recent(self, *, limit: int, include_all: bool) -> list[Any]: ...
    def switch_execution(
        self,
        *,
        connection_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        context_window: int | None,
    ) -> ExecutionProfile: ...
    def set_access_preset(
        self, access_preset: ExecutionAccessPreset | None
    ) -> Thread: ...
    def set_agent_preset(self, preset_id: str | None) -> Thread: ...
    def current_agent_preset_id(self) -> str | None: ...
    def refresh_thread(self) -> Thread: ...
    def clear_context(self) -> None: ...
    async def compact_context(self) -> dict: ...
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None: ...
    async def start_domain_events(
        self, sink: Callable[[DomainEvent], None]
    ) -> None: ...
    async def stop_domain_events(self) -> None: ...
    async def close(self) -> None: ...
