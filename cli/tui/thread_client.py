"""CLI adapter over the shared application Thread/Turn runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from cli.project_trust import (
    open_workspace_project,
    require_project_trusted,
    set_project_trusted,
)
from cli.tui.domain_events import DurableThreadEventCursor
from core.agent_presets import METADATA_KEY as PRESET_METADATA_KEY
from core.application.agent_adapter import ConfiguredAgentSessionFactory
from core.application.application import DeepCodeApplication
from core.application.interactive_turn_router import (
    InteractiveDelivery,
    InteractiveTurnResult,
    InteractiveTurnRouter,
)
from core.domain.approval import Approval, ApprovalStatus
from core.domain.common import new_id
from core.domain.event import DomainEvent
from core.domain.execution_profile import ExecutionProfile, ExecutionSelection
from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
)
from core.domain.message_provenance import ClientSurface
from core.domain.project import TrustState
from core.domain.thread import Thread
from core.domain.turn import Turn, TurnStatus
from core.events import Event
from core.harness.permissions import PermissionMode
from core.sessions import SessionStore, get_default_store


@dataclass(frozen=True, slots=True)
class TuiDelivery:
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


class TuiThreadClient:
    """Own one application and expose only interactive CLI operations."""

    def __init__(
        self,
        *,
        workspace: str,
        model: str | None,
        connection_id: str | None,
        reasoning_effort: str | None,
        max_iterations: int | None,
        streaming: bool,
        trust_workspace: bool = False,
        resume_id: str | None = None,
        store: SessionStore | None = None,
        event_sink: Callable[[Event], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.store = store or get_default_store()
        self._event_sink = event_sink
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._thread_event_token: str | None = None
        self._domain_token: str | None = None
        self._domain_task: asyncio.Task[None] | None = None
        self._domain_cursor: DurableThreadEventCursor | None = None
        self._factory = ConfiguredAgentSessionFactory(
            default_permission_mode=PermissionMode.DEFAULT,
            streaming=streaming,
            max_iterations=max_iterations,
        )
        self.application = DeepCodeApplication.open(
            session_factory=self._factory,
            session_store=self.store,
            host_surface="cli",
            run_automation_scheduler=False,
        )
        try:
            if resume_id is None:
                project = open_workspace_project(
                    self.application,
                    workspace,
                    grant_trust=trust_workspace,
                )
            else:
                existing = self.application.threads.read(resume_id)
                project = self.application.projects.read(existing.project_id)
                if trust_workspace:
                    project = set_project_trusted(self.application, project)
            require_project_trusted(project)
            self.project = project
            self.router = InteractiveTurnRouter(
                self.application.turns,
                client_surface=ClientSurface.CLI,
            )
            self._requested = ExecutionSelection(
                connection_id=connection_id,
                model_id=model,
                reasoning_effort=reasoning_effort,
            )
            self.thread = self._open_thread(resume_id)
            self.project = self.application.projects.read(self.thread.project_id)
            self._subscribe_thread_events()
            self.execution_profile = self._resolve_selection(self.thread)
        except BaseException:
            self.application.close()
            raise

    @property
    def session_id(self) -> str:
        return self.thread.id

    @property
    def model(self) -> str:
        return self.execution_profile.model_id

    @property
    def access_preset_override(self) -> ExecutionAccessPreset | None:
        return self.thread.access_preset_override

    @property
    def project_trusted(self) -> bool:
        return self.project.trust_state is TrustState.TRUSTED

    def access_summary(self) -> str:
        override = self.thread.access_preset_override
        if override is not None:
            return override.value.replace("_", " ")
        profile = self.application.turns.execution_security_policy.resolve(self.thread)
        if profile.access_preset is not None:
            return f"default ({profile.access_preset.value.replace('_', ' ')})"
        return f"legacy ({profile.permission_mode.value})"

    def frozen_access_summaries(self) -> tuple[str | None, tuple[str, ...]]:
        """Describe immutable executing/queued profiles separately from Session state."""

        turns = self.application.turns.list_for_thread(self.thread.id)
        current = next(
            (
                turn
                for turn in turns
                if turn.status in {TurnStatus.RUNNING, TurnStatus.WAITING_APPROVAL}
            ),
            None,
        )
        queued = tuple(turn for turn in turns if turn.status is TurnStatus.QUEUED)
        return (
            _turn_access_summary(current) if current is not None else None,
            tuple(_turn_access_summary(turn) for turn in queued),
        )

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    async def start_domain_events(
        self,
        sink: Callable[[DomainEvent], None],
    ) -> None:
        if self._domain_task is not None:
            return
        self._reset_domain_subscription(self.thread.id)

        async def pump() -> None:
            while True:
                token = self._domain_token
                cursor = self._domain_cursor
                if token is None or cursor is None:
                    return
                batch = self.application.broker.drain(token)
                if token == self._domain_token and cursor is self._domain_cursor:
                    cursor.consume(batch, sink)
                await asyncio.to_thread(
                    self.application.broker.wait_for_events,
                    token,
                    timeout=0.25,
                )

        self._domain_task = asyncio.create_task(pump())

    async def stop_domain_events(self) -> None:
        task = self._domain_task
        token = self._domain_token
        self._domain_task = None
        self._domain_token = None
        self._domain_cursor = None
        if token is not None:
            self.application.broker.unsubscribe(token)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def send(
        self,
        prompt: str,
        *,
        skill_ids: tuple[str, ...] = (),
    ) -> TuiDelivery:
        active = self.application.turns.executing_for_thread(self.thread.id)
        result: InteractiveTurnResult = self.router.send(
            self.thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            cached_active_turn_id=active.id if active is not None else None,
            skill_ids=skill_ids,
        )
        if result.delivery in {
            InteractiveDelivery.STARTED,
            InteractiveDelivery.QUEUED,
        }:
            self._title_from_first_prompt(prompt)
        return TuiDelivery(result.delivery.value, result.turn)

    def queue(
        self,
        prompt: str,
        *,
        skill_ids: tuple[str, ...] = (),
    ) -> TuiDelivery:
        snapshot = self.application.turns.enqueue(
            self.thread.id,
            prompt=prompt,
            message_id=new_id("tinp"),
            skill_ids=skill_ids,
            client_surface=ClientSurface.CLI,
        )
        self._title_from_first_prompt(prompt)
        return TuiDelivery("queued", snapshot.turn)

    def has_active_turn(self) -> bool:
        return self.application.turns.active_for_thread(self.thread.id) is not None

    def rename_thread(self, title: str) -> Thread:
        self.thread = self.application.threads.rename(self.thread.id, title)
        return self.thread

    def delete_session(self, session_id: str) -> None:
        """Permanently delete a stored session (never the one we are in).

        The deletion service already refuses sessions leased by another
        surface; the current session would trip that lease too, but with a
        message blaming "another surface" — catch it here with an honest one.
        """
        if session_id == self.thread.id:
            raise RuntimeError(
                "cannot delete the session you are in — /new first, then delete it"
            )
        self.application.deletions.delete(session_id)

    def last_terminal_turn(self) -> Turn | None:
        turns = self.application.turns.list_for_thread(self.thread.id)
        for turn in reversed(turns):
            if turn.status.is_terminal:
                return turn
        return None

    def retry_turn(self, turn_id: str) -> Turn:
        # use_current_selection: the natural CLI flow is "the turn failed,
        # I fixed /model or /effort, run it again" — replaying the old
        # profile would silently undo that fix.
        snapshot = self.application.turns.retry(turn_id, use_current_selection=True)
        return snapshot.turn

    def interrupt(self) -> tuple[bool, Turn] | None:
        active = self.application.turns.active_for_thread(self.thread.id)
        if active is None:
            return None
        return self.application.turns.interrupt(self.thread.id, active.id)

    def pending_approval(self) -> Approval | None:
        active = self.application.turns.executing_for_thread(self.thread.id)
        if active is None:
            return None
        snapshot = self.application.turns.read(active.id)
        return next(
            (
                approval
                for approval in snapshot.approvals
                if approval.status is ApprovalStatus.PENDING
            ),
            None,
        )

    def respond_to_approval(
        self,
        approval_id: str,
        decision: ApprovalStatus,
    ) -> Approval:
        return self.application.approvals.respond(
            approval_id,
            decision=decision,
        )

    async def wait_until_idle(self) -> None:
        while self.application.turns.active_for_thread(self.thread.id) is not None:
            await asyncio.sleep(0.02)
        # Live SQ/EQ events originate on the execution worker and are forwarded
        # with call_soon_threadsafe. They are enqueued before the Turn becomes
        # terminal, so one event-loop checkpoint deterministically renders the
        # settled Turn before callers print their next prompt or close the TUI.
        await asyncio.sleep(0)

    def new_thread(self, *, title: str = "") -> Thread:
        self._require_idle()
        thread = self.application.threads.start(
            self.project.id,
            title=title.strip() or "New task",
            session_kind="tui",
            connection_id=self.execution_profile.connection_id,
            model=self.execution_profile.model_id,
            reasoning_effort=self._requested.reasoning_effort,
            context_window=self._requested.context_window,
            workspace_path=self.workspace,
        )
        self._replace_thread(thread)
        return self.thread

    def resume(self, session_id: str) -> Thread:
        self._require_idle()
        existing = self.application.threads.read(session_id)
        project = self.application.projects.read(existing.project_id)
        require_project_trusted(project)
        thread = self.application.threads.resume(
            session_id,
            workspace_path=self.workspace,
        )
        self._replace_thread(thread)
        # Resuming into this directory may re-home the thread onto this
        # directory's project — re-read it from the thread, exactly like the
        # constructor does. Keeping the pre-resume project made every later
        # ``threads.start`` fail its workspace-scope check and pointed
        # skills/plugins/MCP at the wrong project.
        self.project = self.application.projects.read(thread.project_id)
        self.execution_profile = self._resolve_selection(self.thread)
        return self.thread

    def list_recent(
        self,
        *,
        limit: int,
        include_all: bool,
    ) -> list[ThreadListing]:
        """Recent resumable Threads, newest first.

        Delegates scoping to ``threads.list`` — the same service the Desktop
        uses — so directory filtering happens on resolved paths *before* the
        limit is applied. (The previous store-level listing truncated
        globally first, which silently dropped this directory's sessions,
        and compared unresolved paths, which never matched through
        symlinks.) Sessions without any message yet are noise, not history.
        """
        threads = self.application.threads.list(
            cwd=None if include_all else self.workspace,
            limit=limit,
        )
        listings = []
        for thread in threads:
            session = self.application.session_store.get_session(thread.id)
            if session is None or not session.messages:
                continue
            listings.append(
                ThreadListing(
                    session_id=thread.id,
                    title=thread.title,
                    message_count=len(session.messages),
                    updated_at=thread.updated_at,
                    workspace=thread.workspace_path,
                    is_current=thread.id == self.thread.id,
                )
            )
        return listings

    def switch_execution(
        self,
        *,
        connection_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        context_window: int | None,
    ) -> ExecutionProfile:
        selection = ExecutionSelection(
            connection_id=connection_id,
            model_id=model,
            reasoning_effort=reasoning_effort,
            context_window=context_window,
        )
        profile = self.application.llm.resolve(self.workspace, selection)
        self.thread = self.application.threads.set_execution_selection(
            self.thread.id,
            connection_id=profile.connection_id,
            model=profile.model_id,
            reasoning_effort=reasoning_effort,
            context_window=context_window,
        )
        self._requested = selection
        self.execution_profile = profile
        return profile

    def set_access_preset(
        self,
        access_preset: ExecutionAccessPreset | None,
    ) -> Thread:
        self.thread = self.application.threads.set_access_preset(
            self.thread.id,
            access_preset,
        )
        return self.thread

    def set_agent_preset(self, preset_id: str | None) -> Thread:
        self.thread = self.application.threads.set_agent_preset(
            self.thread.id,
            preset_id,
        )
        return self.thread

    def current_agent_preset_id(self) -> str | None:
        session = self.application.session_store.get_session(self.thread.id)
        if session is None:
            return None
        value = session.metadata.get(PRESET_METADATA_KEY)
        return value.get("id") if isinstance(value, dict) else None

    def refresh_thread(self) -> Thread:
        self.thread = self.application.threads.read(self.thread.id)
        return self.thread

    def clear_context(self) -> None:
        self._require_idle()
        self.application.turns.clear_live_context(self.thread.id)

    async def compact_context(self) -> dict:
        self._require_idle()
        return await self.application.turns.compact_live_context(self.thread.id)

    async def close(self) -> None:
        await self.stop_domain_events()
        token = self._thread_event_token
        self._thread_event_token = None
        if token is not None:
            self.application.turns.unsubscribe_thread_events(token)
        self.application.close()

    def _open_thread(self, resume_id: str | None) -> Thread:
        if resume_id is None:
            profile = self.application.llm.resolve(self.workspace, self._requested)
            return self.application.threads.start(
                self.project.id,
                title="New task",
                session_kind="tui",
                connection_id=profile.connection_id,
                model=profile.model_id,
                reasoning_effort=self._requested.reasoning_effort,
                context_window=self._requested.context_window,
                workspace_path=self.workspace,
            )
        thread = self.application.threads.resume(
            resume_id,
            workspace_path=self.workspace,
        )
        stored_selection = ExecutionSelection(
            connection_id=self._requested.connection_id or thread.connection_id,
            model_id=self._requested.model_id or thread.model,
            reasoning_effort=(
                self._requested.reasoning_effort
                if self._requested.reasoning_effort is not None
                else thread.reasoning_effort
            ),
            context_window=(
                self._requested.context_window
                if self._requested.context_window is not None
                else thread.context_window
            ),
        )
        self._requested = stored_selection
        profile = self.application.llm.resolve(self.workspace, stored_selection)
        return self.application.threads.set_execution_selection(
            thread.id,
            connection_id=profile.connection_id,
            model=profile.model_id,
            reasoning_effort=stored_selection.reasoning_effort,
            context_window=stored_selection.context_window,
        )

    def _resolve_selection(self, thread: Thread) -> ExecutionProfile:
        # The Thread's stored selection is the single source of truth — the
        # same values a Turn resolves. Falling back to the previously
        # requested selection made the displayed effort/model contradict
        # what the next Turn would actually run with after a resume.
        selection = ExecutionSelection(
            connection_id=thread.connection_id,
            model_id=thread.model,
            reasoning_effort=thread.reasoning_effort,
            context_window=thread.context_window,
        )
        self._requested = selection
        return self.application.llm.resolve(self.workspace, selection)

    def _observe(self, event: Event) -> None:
        if self._event_sink is None:
            return
        loop = self._event_loop
        if loop is None:
            self._event_sink(event)
            return
        loop.call_soon_threadsafe(self._event_sink, event)

    def _subscribe_thread_events(self) -> None:
        self._thread_event_token = self.application.turns.subscribe_thread_events(
            self.thread.id,
            self._observe,
        )

    def _replace_thread(self, thread: Thread) -> None:
        new_token = self.application.turns.subscribe_thread_events(
            thread.id,
            self._observe,
        )
        previous_token = self._thread_event_token
        self.thread = thread
        self._thread_event_token = new_token
        if self._domain_task is not None:
            self._reset_domain_subscription(thread.id)
        if previous_token is not None:
            self.application.turns.unsubscribe_thread_events(previous_token)

    def _reset_domain_subscription(self, thread_id: str) -> None:
        """Start at history head, then replay the head/subscribe race."""

        cursor = DurableThreadEventCursor.at_head(
            self.application.events,
            thread_id,
        )
        token = self.application.broker.subscribe()
        previous = self._domain_token
        self._domain_cursor = cursor
        self._domain_token = token
        if previous is not None:
            self.application.broker.unsubscribe(previous)

    def _title_from_first_prompt(self, prompt: str) -> None:
        if self.thread.title != "New task":
            return
        title = prompt.strip().splitlines()[0][:60]
        if title:
            self.thread = self.application.threads.rename(self.thread.id, title)

    def _require_idle(self) -> None:
        active = self.application.turns.active_for_thread(self.thread.id)
        if active is not None:
            raise RuntimeError(
                "the current Turn is still active; stop it before changing Session"
            )


def _turn_access_summary(turn: Turn) -> str:
    profile: ExecutionSecurityProfile | None = turn.execution_security_profile
    if profile is not None:
        if profile.access_preset is not None:
            return profile.access_preset.value.replace("_", " ")
        sandbox = "sandboxed" if profile.command_sandbox else "unsandboxed"
        return f"legacy {profile.permission_mode.value.replace('_', ' ')} · {sandbox}"
    if turn.execution_permission_mode is not None:
        return f"legacy {turn.execution_permission_mode.value.replace('_', ' ')}"
    return "legacy unknown"


__all__ = ["TuiDelivery", "TuiThreadClient"]
