"""Long-lived AgentSession runtimes keyed by canonical SessionStore identity."""

from __future__ import annotations

import inspect
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from core.agent_runtime.goal_runtime import (
    GoalRuntimeContext,
    GoalRuntimeHandler,
    GoalRuntimeRouter,
)
from core.agent_runtime.injections import (
    TurnInputMailbox,
    TurnInputReservation,
    TurnRuntimeInput,
)
from core.application.agent_adapter import (
    AgentSessionFactory,
    AgentSessionPort,
    ApprovalCallback,
)
from core.application.errors import ConflictError, ThreadNotFoundError
from core.domain.execution_permission import ExecutionPermissionMode
from core.domain.execution_profile import ExecutionProfile
from core.sessions import Session, SessionStore
from core.sessions.continuation import session_message_history_entry


class ApprovalRouter:
    """Keep a stable AgentSession callback while Turns provide fresh context."""

    def __init__(self) -> None:
        self.current: ApprovalCallback | None = None

    async def __call__(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str | None,
    ) -> bool:
        callback = self.current
        if callback is None:
            return False
        result = callback(tool_name, arguments, reason)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)


@dataclass(slots=True)
class LiveSessionRuntime:
    session_id: str
    workspace: str
    model: str | None
    execution_profile: ExecutionProfile | None
    permission_mode_override: ExecutionPermissionMode | None
    agent: AgentSessionPort
    approvals: ApprovalRouter
    canonical_message_count: int
    runtime_key: object
    inputs: TurnInputMailbox
    inputs_enabled: bool
    goals: GoalRuntimeRouter
    goals_enabled: bool
    active: bool = False


class SessionRuntimeRegistry:
    """Retain one AgentSession per loaded Thread, with bounded idle residency."""

    def __init__(
        self,
        store: SessionStore,
        factory: AgentSessionFactory,
        *,
        max_live_sessions: int = 16,
    ) -> None:
        if max_live_sessions < 1:
            raise ValueError("max_live_sessions must be positive")
        self.store = store
        self.factory = factory
        self.max_live_sessions = max_live_sessions
        self._runtimes: OrderedDict[str, LiveSessionRuntime] = OrderedDict()
        self._mailbox_lock = threading.Lock()
        self._mailboxes: dict[str, TurnInputMailbox] = {}
        self._goal_handler: GoalRuntimeHandler | None = None
        self._goal_runtimes: dict[str, GoalRuntimeRouter] = {}

    def configure_goal_handler(self, handler: GoalRuntimeHandler) -> None:
        """Attach application persistence without rebuilding live sessions."""

        with self._mailbox_lock:
            self._goal_handler = handler
            for runtime in self._goal_runtimes.values():
                runtime.configure(handler)

    async def acquire(
        self,
        session_id: str,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        permission_mode_override: ExecutionPermissionMode | None = None,
        approval_callback: ApprovalCallback,
    ) -> AgentSessionPort:
        canonical = self.store.get_session(session_id)
        if canonical is None:
            raise ThreadNotFoundError(f"session not found: {session_id}")

        runtime_key = self._runtime_key(
            workspace=workspace,
            model=model,
            execution_profile=execution_profile,
            permission_mode_override=permission_mode_override,
        )
        runtime = self._runtimes.pop(session_id, None)
        if runtime is not None and runtime.active:
            self._runtimes[session_id] = runtime
            raise ConflictError(f"session runtime is already active: {session_id}")
        if runtime is not None and runtime.runtime_key != runtime_key:
            await runtime.agent.aclose()
            runtime = None
        if runtime is None:
            runtime = self._create(
                canonical,
                workspace=workspace,
                model=model,
                execution_profile=execution_profile,
                permission_mode_override=permission_mode_override,
                runtime_key=runtime_key,
            )
        elif runtime.canonical_message_count != len(canonical.messages):
            # Another DeepCode process appended to the shared Session. Visible
            # JSONL history wins; reloading is safer than silently forking.
            runtime.agent.load_history(self._visible_history(canonical))
            runtime.canonical_message_count = len(canonical.messages)

        runtime.approvals.current = approval_callback
        runtime.active = True
        self._runtimes[session_id] = runtime
        await self._evict_idle()
        return runtime.agent

    def prepare_inputs(self, session_id: str, *, turn_id: str) -> None:
        """Claim input ownership before asynchronous Session startup.

        A coordinator claim is durable before the AgentSession coroutine gets
        CPU time. Preparing the mailbox at that boundary lets an immediate
        Steer wait for activation instead of observing a false ``closed``
        state. Whether the adapter supports live input is a factory capability,
        not a property of an already-created runtime.
        """

        if not _accepts_keyword(self.factory.create, "injection_callback"):
            return
        self._mailbox(session_id).prepare(turn_id)

    def activate_inputs(self, session_id: str, *, turn_id: str) -> None:
        """Open injection after the Turn's first user message is durable."""

        runtime = self._runtimes.get(session_id)
        if runtime is None or not runtime.active:
            raise ConflictError(f"session runtime is not active: {session_id}")
        if not runtime.inputs_enabled:
            return
        runtime.inputs.activate(turn_id)

    def activate_goal(
        self,
        session_id: str,
        *,
        context: GoalRuntimeContext,
    ) -> None:
        """Expose Goal tools only for the owning Goal-associated Turn."""

        runtime = self._runtimes.get(session_id)
        if runtime is None or not runtime.active:
            raise ConflictError(f"session runtime is not active: {session_id}")
        if not runtime.goals_enabled:
            return
        runtime.goals.activate(context)

    def release(self, session_id: str, *, turn_id: str | None = None) -> None:
        runtime = self._runtimes.get(session_id)
        if turn_id is not None:
            self._mailbox(session_id).deactivate(turn_id)
            self._goal_runtime(session_id).deactivate(turn_id)
        if runtime is None:
            return
        runtime.approvals.current = None
        runtime.active = False

    def reserve_input(
        self,
        session_id: str,
        value: TurnRuntimeInput,
    ) -> TurnInputReservation | None:
        """Reserve bounded input only while the expected Turn is active."""

        return self._mailbox(session_id).reserve(value)

    def commit_input(
        self,
        session_id: str,
        reservation: TurnInputReservation,
    ) -> None:
        self._mailbox(session_id).commit(reservation)

    def cancel_input(
        self,
        session_id: str,
        reservation: TurnInputReservation,
    ) -> None:
        self._mailbox(session_id).cancel(reservation)

    def inject_transient(
        self,
        session_id: str,
        value: TurnRuntimeInput,
    ) -> bool:
        """Inject ledger-backed internal context without duplicating persistence."""

        return self._mailbox(session_id).put_transient(value)

    def mark_persisted(self, session_id: str) -> None:
        runtime = self._runtimes.get(session_id)
        canonical = self.store.get_session(session_id)
        if runtime is not None and canonical is not None:
            runtime.canonical_message_count = len(canonical.messages)

    def clear_live_history(self, session_id: str) -> None:
        """Clear only the resident model context, preserving canonical history."""

        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return
        if runtime.active:
            raise ConflictError(f"session runtime is active: {session_id}")
        canonical = self.store.get_session(session_id)
        if canonical is None:
            raise ThreadNotFoundError(f"session not found: {session_id}")
        runtime.agent.load_history([])
        runtime.canonical_message_count = len(canonical.messages)

    async def discard(self, session_id: str) -> None:
        """Close and forget one idle runtime after permanent Session deletion."""

        runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            with self._mailbox_lock:
                self._mailboxes.pop(session_id, None)
                self._goal_runtimes.pop(session_id, None)
            return
        if runtime.active:
            self._runtimes[session_id] = runtime
            raise ConflictError(f"session runtime is active: {session_id}")
        runtime.approvals.current = None
        await runtime.agent.aclose()
        with self._mailbox_lock:
            self._mailboxes.pop(session_id, None)
            self._goal_runtimes.pop(session_id, None)

    async def close_all(self) -> None:
        runtimes = tuple(self._runtimes.values())
        self._runtimes.clear()
        for runtime in runtimes:
            runtime.approvals.current = None
            try:
                await runtime.agent.aclose()
            except Exception:
                # Shutdown must continue so every other session gets a chance
                # to release AgentControl and tool subprocesses.
                continue
        with self._mailbox_lock:
            self._mailboxes.clear()
            self._goal_runtimes.clear()

    @property
    def live_session_ids(self) -> tuple[str, ...]:
        return tuple(self._runtimes)

    def _create(
        self,
        canonical: Session,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None,
        permission_mode_override: ExecutionPermissionMode | None,
        runtime_key: object,
    ) -> LiveSessionRuntime:
        approvals = ApprovalRouter()
        inputs = self._mailbox(canonical.session_id)
        goals = self._goal_runtime(canonical.session_id)
        create = self.factory.create
        create_kwargs = {
            "workspace": workspace,
            "model": model,
            "approval_callback": approvals,
        }
        if _accepts_keyword(create, "execution_profile"):
            create_kwargs["execution_profile"] = execution_profile
        if _accepts_keyword(create, "permission_mode_override"):
            create_kwargs["permission_mode_override"] = permission_mode_override
        inputs_enabled = _accepts_keyword(create, "injection_callback")
        if inputs_enabled:
            create_kwargs["injection_callback"] = inputs.drain
        if _accepts_keyword(create, "active_turn_id_provider"):
            create_kwargs["active_turn_id_provider"] = lambda: inputs.active_turn_id
        if _accepts_keyword(create, "runtime_input_sink"):
            create_kwargs["runtime_input_sink"] = inputs.put_transient
        goals_enabled = _accepts_keyword(create, "goal_runtime")
        if goals_enabled:
            create_kwargs["goal_runtime"] = goals
        agent = create(**create_kwargs)
        agent.load_history(self._visible_history(canonical))
        return LiveSessionRuntime(
            session_id=canonical.session_id,
            workspace=workspace,
            model=model,
            execution_profile=execution_profile,
            permission_mode_override=permission_mode_override,
            agent=agent,
            approvals=approvals,
            canonical_message_count=len(canonical.messages),
            runtime_key=runtime_key,
            inputs=inputs,
            inputs_enabled=inputs_enabled,
            goals=goals,
            goals_enabled=goals_enabled,
        )

    def _mailbox(self, session_id: str) -> TurnInputMailbox:
        with self._mailbox_lock:
            mailbox = self._mailboxes.get(session_id)
            if mailbox is None:
                mailbox = TurnInputMailbox()
                self._mailboxes[session_id] = mailbox
            return mailbox

    def _goal_runtime(self, session_id: str) -> GoalRuntimeRouter:
        with self._mailbox_lock:
            runtime = self._goal_runtimes.get(session_id)
            if runtime is None:
                runtime = GoalRuntimeRouter()
                if self._goal_handler is not None:
                    runtime.configure(self._goal_handler)
                self._goal_runtimes[session_id] = runtime
            return runtime

    def _runtime_key(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None,
        permission_mode_override: ExecutionPermissionMode | None,
    ) -> object:
        resolver = getattr(self.factory, "runtime_key", None)
        if callable(resolver):
            kwargs = {"workspace": workspace, "model": model}
            if _accepts_keyword(resolver, "execution_profile"):
                kwargs["execution_profile"] = execution_profile
            if _accepts_keyword(resolver, "permission_mode_override"):
                kwargs["permission_mode_override"] = permission_mode_override
            factory_key = resolver(**kwargs)
        else:
            factory_key = (
                workspace,
                model,
                (
                    execution_profile.connection_id,
                    execution_profile.config_revision,
                    execution_profile.context_window,
                    execution_profile.max_output_tokens,
                    execution_profile.max_tokens,
                    execution_profile.temperature,
                    execution_profile.reasoning_effort,
                )
                if execution_profile
                else None,
            )
        return (
            factory_key,
            (
                permission_mode_override.value
                if permission_mode_override is not None
                else None
            ),
        )

    async def _evict_idle(self) -> None:
        while len(self._runtimes) > self.max_live_sessions:
            victim_id = next(
                (
                    session_id
                    for session_id, runtime in self._runtimes.items()
                    if not runtime.active
                ),
                None,
            )
            if victim_id is None:
                return
            victim = self._runtimes.pop(victim_id)
            await victim.agent.aclose()

    @staticmethod
    def _visible_history(session: Session) -> list[dict[str, Any]]:
        return [
            session_message_history_entry(message)
            for message in session.messages
            if message.role in {"user", "assistant"} and message.content
        ]


def _accepts_keyword(callable_object, name: str) -> bool:
    parameters = inspect.signature(callable_object).parameters.values()
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == name
        for parameter in parameters
    )


__all__ = [
    "ApprovalRouter",
    "LiveSessionRuntime",
    "SessionRuntimeRegistry",
]
