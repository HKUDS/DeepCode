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
from functools import partial
from typing import Any

from loguru import logger

from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.catalog import context_window_for
from core.events.protocol import (
    AgentMessage,
    AgentMessageDelta,
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
    summarize_call,
    summarize_result,
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

    def __init__(self, emit, *, streaming: bool = False) -> None:
        super().__init__()
        self._emit = emit
        self._streaming = streaming

    def wants_streaming(self) -> bool:
        # Routes the kernel through chat_stream_with_retry so assistant text
        # arrives as deltas; each delta is forwarded onto the event queue.
        return self._streaming

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        if delta:
            self._emit(AgentMessageDelta(delta=delta))

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for call in context.tool_calls:
            self._emit(
                ToolStarted(
                    call_id=call.id,
                    name=call.name,
                    detail=summarize_call(call.name, call.arguments),
                )
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        for call, result in zip(context.tool_calls, context.tool_results):
            self._emit(
                ToolCompleted(
                    call_id=call.id,
                    name=call.name,
                    is_error=_is_error_result(result),
                    result_preview=summarize_result(result),
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
        injection_callback: Any | None = None,
        hooks_engine: Any | None = None,
        agent_context: tuple[str, str] | None = None,
        context_window_tokens: int | None = None,
        streaming: bool = False,
    ) -> None:
        self._runner = AgentRunner(provider)
        self._provider = provider
        self._tools = tools
        self._model = model
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._permission_checker = permission_checker
        self._approval_callback = approval_callback
        # Drains delegated sub-agents' results into this turn (see AgentControl).
        self._injection_callback = injection_callback
        # External-command hooks (C3). Fires SessionStart (once) + UserPromptSubmit
        # (each prompt) here, and PreToolUse/PostToolUse in the runner. None when
        # no hooks are configured, so the whole feature is dormant at zero cost.
        self._hooks_engine = hooks_engine
        self._session_started = False
        # When this session is a spawned sub-agent, (agent_id, agent_type) — its
        # lifecycle fires SubagentStart/SubagentStop instead of SessionStart/Stop.
        self._agent_context = agent_context
        # Context-window budget that arms the runner's compaction ladder
        # (_snip_history / _microcompact). Left unset it stays dormant and a
        # long enough session overflows the model; resolving it from the
        # model catalog is what makes "long sessions don't crash" (P2 exit
        # criterion) true for every AgentSession frontend — exec, TUI, web.
        self._context_window_tokens = context_window_tokens or context_window_for(model)
        # When on, assistant text streams out as AgentMessageDelta events
        # (terminated by the authoritative AgentMessage). Interactive
        # frontends enable this; headless NDJSON consumers leave it off.
        self._streaming = streaming

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

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        """Replace the conversation history (session resume).

        ``messages`` are chat-format dicts (``{"role", "content"}``); the
        system prompt is prepended per turn, so it must not be included.
        """
        self._history = [dict(m) for m in messages]

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

    async def _run_start_hook(self):
        """Run SessionStart, or SubagentStart when this is a sub-agent session.

        Returns the start outcome (context + optional block) or ``None`` if the
        event isn't configured / the hook failed (failures are logged, not fatal).
        """
        engine = self._hooks_engine
        try:
            if self._agent_context is not None:
                if not engine.has_event("SubagentStart"):
                    return None
                agent_id, agent_type = self._agent_context
                return await engine.run_subagent_start(agent_id, agent_type)
            if not engine.has_event("SessionStart"):
                return None
            return await engine.run_session_start("startup")
        except Exception:
            logger.exception("start hook failed")
            return None

    async def _run_prompt_hooks(self, text: str, hook_contexts: list[str]) -> str | None:
        """Run SessionStart (once) + UserPromptSubmit hooks.

        Appends any injected context to ``hook_contexts`` and returns a block
        message if UserPromptSubmit blocked the turn, else ``None``. A hook
        failure is logged and ignored — hooks never crash a turn.
        """
        engine = self._hooks_engine
        if not self._session_started:
            self._session_started = True
            out = await self._run_start_hook()
            if out is not None:
                hook_contexts.extend(out.additional_contexts)
                if out.block:
                    return out.block_reason or "Session blocked by a start hook."
        if engine.has_event("UserPromptSubmit"):
            try:
                out = await engine.run_user_prompt_submit(text)
            except Exception:
                logger.exception("UserPromptSubmit hook failed")
                return None
            hook_contexts.extend(out.additional_contexts)
            if out.block:
                return out.block_reason or "Prompt blocked by a UserPromptSubmit hook."
        return None

    async def _run_user_input(self, text: str) -> None:
        if self._busy:
            self._emit(ErrorEvent(message="a turn is already in progress"))
            return
        self._busy = True
        self._emit(TurnStarted())

        # External-command hooks (C3): SessionStart (once) + UserPromptSubmit
        # (every prompt). UserPromptSubmit may block the turn outright or inject
        # context; SessionStart injects session context. Injected context rides
        # as system messages ahead of history so the model reads it this turn.
        hook_contexts: list[str] = []
        if self._hooks_engine is not None:
            blocked = await self._run_prompt_hooks(text, hook_contexts)
            if blocked is not None:
                self._history.append({"role": "user", "content": text})
                self._emit(TaskComplete(final_text=blocked, stop_reason="blocked_by_hook"))
                self._busy = False
                return

        self._history.append({"role": "user", "content": text})

        initial: list[dict[str, Any]] = []
        if self._system_prompt:
            initial.append({"role": "system", "content": self._system_prompt})
        for ctx in hook_contexts:
            initial.append({"role": "system", "content": ctx})
        initial.extend(self._history)

        pre_tool_hook = post_tool_hook = permission_request_hook = stop_hook = None
        if self._hooks_engine is not None:
            if self._hooks_engine.has_event("PreToolUse"):
                pre_tool_hook = self._hooks_engine.run_pre_tool_use
            if self._hooks_engine.has_event("PostToolUse"):
                post_tool_hook = self._hooks_engine.run_post_tool_use
            if self._hooks_engine.has_event("PermissionRequest"):
                permission_request_hook = self._hooks_engine.run_permission_request
            if self._agent_context is not None:
                if self._hooks_engine.has_event("SubagentStop"):
                    agent_id, agent_type = self._agent_context
                    stop_hook = partial(
                        self._hooks_engine.run_subagent_stop, agent_id, agent_type
                    )
            elif self._hooks_engine.has_event("Stop"):
                stop_hook = self._hooks_engine.run_stop

        spec = AgentRunSpec(
            initial_messages=initial,
            tools=self._tools,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=_DEFAULT_MAX_TOOL_RESULT_CHARS,
            context_window_tokens=self._context_window_tokens,
            hook=_EventEmittingHook(self._emit, streaming=self._streaming),
            permission_checker=self._permission_checker,
            approval_callback=self._approval_callback,
            injection_callback=self._injection_callback,
            pre_tool_hook=pre_tool_hook,
            post_tool_hook=post_tool_hook,
            permission_request_hook=permission_request_hook,
            stop_hook=stop_hook,
        )

        try:
            self._current_task = asyncio.ensure_future(self._runner.run(spec))
            result = await self._current_task
        except asyncio.CancelledError:
            self._emit(TaskComplete(final_text=None, stop_reason="interrupted"))
            self._busy = False
            self._current_task = None
            return
        except Exception as exc:  # noqa: BLE001
            # The runner should return errors as data, but a truly unexpected
            # exception must still terminate the turn — otherwise a consumer
            # blocked on the next event (run_stream) would hang forever. Always
            # close the turn with an error + task_complete.
            self._emit(ErrorEvent(message=f"{type(exc).__name__}: {exc}"))
            self._emit(TaskComplete(final_text=None, stop_reason="error"))
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
