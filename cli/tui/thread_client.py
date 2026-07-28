"""CLI adapter over the shared application Thread/Turn runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

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
from core.domain.message_provenance import ClientSurface
from core.domain.project import TrustState
from core.domain.thread import Thread
from core.domain.turn import Turn
from core.events import Event
from core.harness.permissions import PermissionMode
from core.harness.policy import build_permission_engine
from core.config import load_config_for_workspace
from core.sessions import SessionStore, get_default_store


@dataclass(frozen=True, slots=True)
class TuiDelivery:
    kind: str
    turn: Turn


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
        resume_id: str | None = None,
        store: SessionStore | None = None,
        event_sink: Callable[[Event], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.store = store or get_default_store()
        self._event_sink = event_sink
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._domain_token: str | None = None
        self._domain_task: asyncio.Task[None] | None = None
        self._factory = ConfiguredAgentSessionFactory(
            default_permission_mode=PermissionMode.FULL_AUTO,
            streaming=streaming,
            max_iterations=max_iterations,
        )
        self.application = DeepCodeApplication.open(
            session_factory=self._factory,
            session_store=self.store,
        )
        try:
            project = self.application.projects.add(
                workspace,
                trust_state=TrustState.TRUSTED,
            )
            if project.trust_state is not TrustState.TRUSTED:
                project = self.application.projects.update(
                    project.id,
                    trust_state=TrustState.TRUSTED,
                )
            self.project = project
            self.router = InteractiveTurnRouter(
                self.application.turns,
                client_surface=ClientSurface.CLI,
            )
            self.permission_mode = build_permission_engine(
                load_config_for_workspace(workspace).security,
                cwd=workspace,
                default_mode=PermissionMode.FULL_AUTO,
            ).mode
            self._requested = ExecutionSelection(
                connection_id=connection_id,
                model_id=model,
                reasoning_effort=reasoning_effort,
            )
            self.thread = self._open_thread(resume_id)
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

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    async def start_domain_events(
        self,
        sink: Callable[[DomainEvent], None],
    ) -> None:
        if self._domain_task is not None:
            return
        self._domain_token = self.application.broker.subscribe()

        async def pump() -> None:
            assert self._domain_token is not None
            while True:
                batch = self.application.broker.drain(self._domain_token)
                for event in batch.events:
                    if event.thread_id == self.thread.id:
                        sink(event)
                await asyncio.to_thread(
                    self.application.broker.wait_for_events,
                    self._domain_token,
                    timeout=0.25,
                )

        self._domain_task = asyncio.create_task(pump())

    async def stop_domain_events(self) -> None:
        task = self._domain_task
        token = self._domain_token
        self._domain_task = None
        self._domain_token = None
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
            event_observer=self._observe,
        )
        if result.delivery is InteractiveDelivery.STARTED:
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
            event_observer=self._observe,
            client_surface=ClientSurface.CLI,
        )
        self._title_from_first_prompt(prompt)
        return TuiDelivery("queued", snapshot.turn)

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

    def new_thread(self, *, title: str = "") -> Thread:
        self._require_idle()
        self.thread = self.application.threads.start(
            self.project.id,
            title=title.strip() or "New task",
            session_kind="tui",
            connection_id=self.execution_profile.connection_id,
            model=self.execution_profile.model_id,
            reasoning_effort=self._requested.reasoning_effort,
            workspace_path=self.workspace,
        )
        return self.thread

    def resume(self, session_id: str) -> Thread:
        self._require_idle()
        self.thread = self.application.threads.resume(
            session_id,
            workspace_path=self.workspace,
        )
        self.execution_profile = self._resolve_selection(self.thread)
        return self.thread

    def switch_execution(
        self,
        *,
        connection_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
    ) -> ExecutionProfile:
        selection = ExecutionSelection(
            connection_id=connection_id,
            model_id=model,
            reasoning_effort=reasoning_effort,
        )
        profile = self.application.llm.resolve(self.workspace, selection)
        self.thread = self.application.threads.set_execution_selection(
            self.thread.id,
            connection_id=profile.connection_id,
            model=profile.model_id,
            reasoning_effort=reasoning_effort,
        )
        self._requested = selection
        self.execution_profile = profile
        return profile

    def refresh_thread(self) -> Thread:
        self.thread = self.application.threads.read(self.thread.id)
        return self.thread

    def clear_context(self) -> None:
        self._require_idle()
        self.application.turns.clear_live_context(self.thread.id)

    async def close(self) -> None:
        await self.stop_domain_events()
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
        )
        self._requested = stored_selection
        profile = self.application.llm.resolve(self.workspace, stored_selection)
        return self.application.threads.set_execution_selection(
            thread.id,
            connection_id=profile.connection_id,
            model=profile.model_id,
            reasoning_effort=stored_selection.reasoning_effort,
        )

    def _resolve_selection(self, thread: Thread) -> ExecutionProfile:
        selection = ExecutionSelection(
            connection_id=thread.connection_id or self._requested.connection_id,
            model_id=thread.model or self._requested.model_id,
            reasoning_effort=(
                thread.reasoning_effort
                if thread.reasoning_effort is not None
                else self._requested.reasoning_effort
            ),
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


__all__ = ["TuiDelivery", "TuiThreadClient"]
