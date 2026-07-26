"""Long-lived AgentSession runtimes keyed by canonical SessionStore identity."""

from __future__ import annotations

import inspect
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from core.application.agent_adapter import (
    AgentSessionFactory,
    AgentSessionPort,
    ApprovalCallback,
)
from core.application.errors import ConflictError, ThreadNotFoundError
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
    agent: AgentSessionPort
    approvals: ApprovalRouter
    canonical_message_count: int
    runtime_key: object
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

    async def acquire(
        self,
        session_id: str,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None = None,
        approval_callback: ApprovalCallback,
    ) -> AgentSessionPort:
        canonical = self.store.get_session(session_id)
        if canonical is None:
            raise ThreadNotFoundError(f"session not found: {session_id}")

        runtime_key = self._runtime_key(
            workspace=workspace,
            model=model,
            execution_profile=execution_profile,
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

    def release(self, session_id: str) -> None:
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return
        runtime.approvals.current = None
        runtime.active = False

    def mark_persisted(self, session_id: str) -> None:
        runtime = self._runtimes.get(session_id)
        canonical = self.store.get_session(session_id)
        if runtime is not None and canonical is not None:
            runtime.canonical_message_count = len(canonical.messages)

    async def discard(self, session_id: str) -> None:
        """Close and forget one idle runtime after permanent Session deletion."""

        runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return
        if runtime.active:
            self._runtimes[session_id] = runtime
            raise ConflictError(f"session runtime is active: {session_id}")
        runtime.approvals.current = None
        await runtime.agent.aclose()

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
        runtime_key: object,
    ) -> LiveSessionRuntime:
        approvals = ApprovalRouter()
        create = self.factory.create
        create_kwargs = {
            "workspace": workspace,
            "model": model,
            "approval_callback": approvals,
        }
        if _accepts_keyword(create, "execution_profile"):
            create_kwargs["execution_profile"] = execution_profile
        agent = create(**create_kwargs)
        agent.load_history(self._visible_history(canonical))
        return LiveSessionRuntime(
            session_id=canonical.session_id,
            workspace=workspace,
            model=model,
            execution_profile=execution_profile,
            agent=agent,
            approvals=approvals,
            canonical_message_count=len(canonical.messages),
            runtime_key=runtime_key,
        )

    def _runtime_key(
        self,
        *,
        workspace: str,
        model: str | None,
        execution_profile: ExecutionProfile | None,
    ) -> object:
        resolver = getattr(self.factory, "runtime_key", None)
        if callable(resolver):
            kwargs = {"workspace": workspace, "model": model}
            if _accepts_keyword(resolver, "execution_profile"):
                kwargs["execution_profile"] = execution_profile
            return resolver(**kwargs)
        return (
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
