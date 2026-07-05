"""AgentSession — the engine behind the SQ/EQ protocol (L1).

Consumes :data:`~core.events.protocol.Op` submissions and emits
:class:`~core.events.protocol.Event` messages onto an event queue, driving
the shared kernel (:class:`~core.agent_runtime.runner.AgentRunner`) for a
turn. This is the reusable seam every frontend attaches to — a TUI, a
headless runner, the web backend, or a test all speak the same protocol and
never touch the kernel directly.

Design:
- one active turn at a time (a new ``UserInput`` while busy is rejected);
- conversation history persists across turns on the session;
- tool lifecycle + completion stream out as events *while* the turn runs,
  via an :class:`_EventEmittingHook` bridged onto the kernel's hook seam —
  so this is a live integration of the event vocabulary, not a post-hoc
  projection.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.registry import ToolRegistry
from core.events.protocol import (
    AgentMessage,
    ErrorEvent,
    Event,
    Interrupt,
    Op,
    Shutdown,
    ShutdownComplete,
    Submission,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
    UserInput,
)
from core.providers.base import LLMProvider

_DEFAULT_MAX_ITERATIONS = 50
_DEFAULT_MAX_TOOL_RESULT_CHARS = 60_000


def _is_error_result(result: Any) -> bool:
    text = result if isinstance(result, str) else str(result)
    stripped = text.lstrip()
    return stripped.startswith("Error") or "permission denied" in stripped[:40]


class _EventEmittingHook(AgentHook):
    """Bridge kernel hook callbacks onto the event queue in real time."""

    def __init__(self, emit) -> None:
        super().__init__()
        self._emit = emit

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for call in context.tool_calls:
            self._emit(ToolStarted(call_id=call.id, name=call.name))

    async def after_iteration(self, context: AgentHookContext) -> None:
        for call, result in zip(context.tool_calls, context.tool_results):
            self._emit(
                ToolCompleted(
                    call_id=call.id,
                    name=call.name,
                    is_error=_is_error_result(result),
                )
            )


class AgentSession:
    """A conversational agent addressed through the SQ/EQ protocol."""

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        *,
        model: str,
        system_prompt: str = "",
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        permission_checker: Any | None = None,
        approval_callback: Any | None = None,
    ) -> None:
        self._runner = AgentRunner(provider)
        self._provider = provider
        self._tools = tools
        self._model = model
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._permission_checker = permission_checker
        self._approval_callback = approval_callback

        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._history: list[dict[str, Any]] = []
        self._seq = 0
        self._busy = False
        self._current_task: asyncio.Task | None = None

    # -- event queue -------------------------------------------------------

    def _emit(self, msg) -> None:
        self._seq += 1
        self._events.put_nowait(Event(id=str(self._seq), msg=msg))

    async def next_event(self) -> Event:
        return await self._events.get()

    async def run_stream(self, op: Op):
        """Submit ``op`` and yield events live until the turn ends.

        The streaming consumer API every frontend uses (``deepcode exec``,
        a TUI, the web backend): events arrive as they happen rather than
        all at once. A ``UserInput`` turn always ends with ``task_complete``
        (even on interrupt/error), and ``Shutdown`` with ``shutdown_complete``,
        so the loop terminates.
        """
        task = asyncio.ensure_future(self.submit(op))
        try:
            while True:
                event = await self.next_event()
                yield event
                if event.msg.type in ("task_complete", "shutdown_complete"):
                    break
        finally:
            await task

    def drain_events(self) -> list[Event]:
        """Non-blocking: pop all currently queued events (handy for tests)."""
        out: list[Event] = []
        while not self._events.empty():
            out.append(self._events.get_nowait())
        return out

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    # -- submission handling ----------------------------------------------

    async def submit(self, op: Op) -> None:
        """Process one submission, emitting events onto the queue."""
        if isinstance(op, UserInput):
            await self._run_user_input(op.text)
        elif isinstance(op, Interrupt):
            if self._current_task is not None and not self._current_task.done():
                self._current_task.cancel()
        elif isinstance(op, Shutdown):
            self._emit(ShutdownComplete())
        else:  # pragma: no cover - exhaustive guard
            self._emit(ErrorEvent(message=f"unknown op: {op!r}"))

    async def submit_envelope(self, submission: Submission) -> None:
        await self.submit(submission.op)

    async def _run_user_input(self, text: str) -> None:
        if self._busy:
            self._emit(ErrorEvent(message="a turn is already in progress"))
            return
        self._busy = True
        self._emit(TurnStarted())
        self._history.append({"role": "user", "content": text})

        initial: list[dict[str, Any]] = []
        if self._system_prompt:
            initial.append({"role": "system", "content": self._system_prompt})
        initial.extend(self._history)

        spec = AgentRunSpec(
            initial_messages=initial,
            tools=self._tools,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=_DEFAULT_MAX_TOOL_RESULT_CHARS,
            hook=_EventEmittingHook(self._emit),
            permission_checker=self._permission_checker,
            approval_callback=self._approval_callback,
        )

        try:
            self._current_task = asyncio.ensure_future(self._runner.run(spec))
            result = await self._current_task
        except asyncio.CancelledError:
            self._emit(TaskComplete(final_text=None, stop_reason="interrupted"))
            self._busy = False
            self._current_task = None
            return
        finally:
            self._current_task = None

        # Persist the turn's messages (minus the system prompt) as history.
        self._history = [m for m in result.messages if m.get("role") != "system"]

        if result.final_content:
            self._emit(AgentMessage(text=result.final_content))
        if result.error and result.stop_reason in ("error", "empty_final_response"):
            self._emit(ErrorEvent(message=result.error))
        self._emit(
            TaskComplete(
                final_text=result.final_content, stop_reason=result.stop_reason
            )
        )
        self._busy = False
