"""Headless adapter over the shared DeepCode Project/Thread/Turn runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cli.project_trust import (
    open_workspace_project,
    require_project_trusted,
    set_project_trusted,
)
from core.application.agent_adapter import ConfiguredAgentSessionFactory
from core.application.application import DeepCodeApplication
from core.domain.approval import Approval, ApprovalStatus
from core.domain.common import new_id
from core.domain.execution_profile import ExecutionSelection
from core.domain.execution_security import ExecutionAccessPreset
from core.domain.message_provenance import ClientSurface
from core.domain.turn import Turn, TurnStatus
from core.events import Event
from core.harness.permissions import PermissionMode
from core.skills.management import LocalSkillManager


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


@dataclass(frozen=True, slots=True)
class HeadlessTurnResult:
    turn: Turn
    session_id: str
    workspace: str


EventHook = Callable[[Event], None]
ApprovalDecider = Callable[[Approval], ApprovalStatus]


def run_headless_turn(
    options: HeadlessTurnOptions,
    *,
    on_event: EventHook | None = None,
    decide_approval: ApprovalDecider | None = None,
) -> HeadlessTurnResult:
    """Run one durable Turn and block until its terminal state is persisted."""

    prompt = options.prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    requested_workspace = (
        str(Path(options.workspace).expanduser().resolve(strict=True))
        if options.workspace is not None
        else None
    )
    if options.resume_id is None and requested_workspace is None:
        requested_workspace = str(Path.cwd().resolve(strict=True))

    factory = ConfiguredAgentSessionFactory(
        default_permission_mode=PermissionMode.DEFAULT,
        streaming=False,
        max_iterations=options.max_iterations,
    )
    application = DeepCodeApplication.open(
        session_factory=factory,
        host_surface="headless",
        run_automation_scheduler=False,
    )
    event_token: str | None = None
    activity_lease = None
    wake = threading.Event()
    terminal_agent_event = threading.Event()
    thread = None

    def observe(event: Event) -> None:
        try:
            if on_event is not None:
                on_event(event)
        finally:
            if event.msg.type in {"task_complete", "error"}:
                terminal_agent_event.set()
            wake.set()

    try:
        if options.resume_id is None:
            assert requested_workspace is not None
            project = open_workspace_project(
                application,
                requested_workspace,
                grant_trust=options.trust_workspace,
            )
            require_project_trusted(project)
            selection = ExecutionSelection(
                connection_id=options.connection_id,
                model_id=options.model,
                reasoning_effort=options.reasoning_effort,
            )
            profile = application.llm.resolve(requested_workspace, selection)
            thread = application.threads.start(
                project.id,
                title=prompt.splitlines()[0][:60],
                session_kind="headless",
                connection_id=profile.connection_id,
                model=profile.model_id,
                reasoning_effort=options.reasoning_effort,
                access_preset_override=options.access_preset,
                workspace_path=requested_workspace,
            )
        else:
            session_id = options.resume_id.strip()
            if not session_id:
                raise ValueError("resume Session ID must not be empty")
            thread = application.threads.resume(
                session_id,
                workspace_path=requested_workspace,
            )
            project = application.projects.read(thread.project_id)
            if options.trust_workspace:
                project = set_project_trusted(application, project)
            require_project_trusted(project)
            if options.access_preset is not None:
                thread = application.threads.set_access_preset(
                    thread.id,
                    options.access_preset,
                )

        workspace = thread.workspace_path
        manager = LocalSkillManager(workspace)
        skill_ids = tuple(
            manager.select(identifier).id for identifier in options.skill_identifiers
        )
        activity_lease = application.session_store.acquire_activity_lease(thread.id)
        if activity_lease is None:
            raise RuntimeError(f"Session disappeared before execution: {thread.id}")
        event_token = application.turns.subscribe_thread_events(thread.id, observe)
        snapshot = application.turns.start(
            thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            skill_ids=skill_ids,
            connection_id=options.connection_id,
            model=options.model,
            reasoning_effort=options.reasoning_effort,
            client_surface=ClientSurface.HEADLESS,
        )
        handled_approvals: set[str] = set()
        while not snapshot.turn.status.is_terminal:
            for approval in snapshot.approvals:
                if (
                    approval.status is not ApprovalStatus.PENDING
                    or approval.id in handled_approvals
                ):
                    continue
                handled_approvals.add(approval.id)
                decision = (
                    decide_approval(approval)
                    if decide_approval is not None
                    else ApprovalStatus.DENIED
                )
                application.approvals.respond(approval.id, decision=decision)
            wake.wait(timeout=0.05)
            wake.clear()
            snapshot = application.turns.read(snapshot.turn.id)

        # A normal Agent Turn emits its terminal SQ/EQ event before terminal
        # persistence. The bounded wait also covers exceptional runtime exits,
        # where no task_complete event is available.
        terminal_agent_event.wait(timeout=0.1)
        return HeadlessTurnResult(
            turn=application.turns.read(snapshot.turn.id).turn,
            session_id=thread.id,
            workspace=workspace,
        )
    except KeyboardInterrupt:
        if thread is not None:
            active = application.turns.active_for_thread(thread.id)
            if active is not None:
                application.turns.interrupt(thread.id, active.id)
        raise
    finally:
        if event_token is not None:
            application.turns.unsubscribe_thread_events(event_token)
        if activity_lease is not None:
            activity_lease.close()
        application.close()


def succeeded(result: HeadlessTurnResult) -> bool:
    return result.turn.status is TurnStatus.COMPLETED


__all__ = [
    "ApprovalDecider",
    "EventHook",
    "HeadlessTurnOptions",
    "HeadlessTurnResult",
    "run_headless_turn",
    "succeeded",
]
