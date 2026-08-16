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
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from loguru import logger

from core.agent_runtime.context import EnvironmentContext
from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runner import AgentRunner, AgentRunSpec
from core.agent_runtime.token_meter import ProviderAnchoredTokenMeter
from core.agent_runtime.tools.base import ToolResult
from core.agent_runtime.tools.registry import ToolRegistry
from core.events.protocol import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    ErrorEvent,
    Event,
    Interrupt,
    ModelUsageRecorded,
    Op,
    Shutdown,
    ShutdownComplete,
    SkillLoaded,
    SkillLoadFailed,
    Submission,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
    UserInput,
    describe_tool_activity,
    parse_plan_update,
    summarize_call,
    summarize_result,
)
from core.mcp.models import McpStartupError
from core.mcp.runtime import McpSessionRuntime
from core.providers.base import LLMProvider
from core.providers.catalog import context_window_for
from core.reasoning import ReasoningAvailability, ReasoningChannel
from core.skills.models import SkillError
from core.skills.runtime import SkillRuntime, SkillTurnContext

_DEFAULT_MAX_TOOL_RESULT_CHARS = 60_000

# P1-4 (GenAI course lesson 05): the tool loop + structured outputs run at a
# low temperature so repeated executions are reproducible (0.1 vs 0.9 variance
# is the lesson's canonical trap). Creative subtasks override explicitly via
# the execution profile / provider default. reasoning/effort models may ignore
# temperature — that is their documented behavior, not a wiring bug.
_DEFAULT_TOOL_LOOP_TEMPERATURE = 0.1


def _one_line_detail(value: str, *, limit: int = 80) -> str:
    """Bound a tool-declared presentation value without inspecting its data."""

    if not isinstance(value, str):
        return ""
    text = value.strip().splitlines()[0] if value.strip() else ""
    return text[:limit] + ("…" if len(text) > limit else "")


def _is_error_result(result: Any) -> bool:
    if isinstance(result, ToolResult):
        return result.is_error
    text = result if isinstance(result, str) else str(result)
    stripped = text.lstrip()
    return stripped.startswith("Error") or "permission denied" in stripped[:40]


def _stop_continuation_reason(outcome: Any) -> str | None:
    if isinstance(outcome, str):
        reason = outcome.strip()
        return reason or None
    if outcome is None or not getattr(outcome, "block", False):
        return None
    reason = getattr(outcome, "block_reason", None)
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


class _EventEmittingHook(AgentHook):
    """Bridge kernel hook callbacks onto the event queue in real time."""

    def __init__(
        self,
        emit,
        *,
        streaming: bool = False,
        emit_deltas: bool | None = None,
        usage_sink=None,
        reasoning_effort: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        super().__init__()
        self._emit = emit
        self._streaming = streaming
        self._emit_deltas = streaming if emit_deltas is None else emit_deltas
        self._usage_sink = usage_sink
        self._reasoning_effort = reasoning_effort
        self._tools = tools
        self._reasoning_context_id: int | None = None
        self._reasoning_id: str | None = None
        self._reasoning_summary = ""
        self._reasoning_trace = ""
        self._reasoning_started_at: float | None = None
        self._message_id: str | None = None
        self._message_text = ""
        self._last_message_id: str | None = None
        self._last_message_text = ""

    def wants_streaming(self) -> bool:
        # Routes the kernel through chat_stream_with_retry so assistant text
        # arrives as deltas; each delta is forwarded onto the event queue.
        return self._streaming

    async def before_model_request(self, context: AgentHookContext) -> None:
        """Arm one displayable provider response.

        Internal compaction requests do not cross this boundary, so their
        reasoning never leaks into the user transcript.
        """

        self._reasoning_context_id = id(context)
        self._reasoning_id = None
        self._reasoning_summary = ""
        self._reasoning_trace = ""
        self._reasoning_started_at = monotonic()

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        if not delta:
            return
        if self._message_id is None:
            self._message_id = uuid4().hex
            self._message_text = ""
        self._message_text += delta
        if self._emit_deltas:
            self._emit(
                AgentMessageDelta(
                    delta=delta,
                    message_id=self._message_id,
                )
            )

    def _ensure_reasoning_started(self) -> str:
        reasoning_id = self._reasoning_id
        if reasoning_id is None:
            reasoning_id = uuid4().hex
            self._reasoning_id = reasoning_id
            self._emit(
                AgentReasoningStarted(
                    reasoning_id=reasoning_id,
                    effort=self._reasoning_effort,
                )
            )
        return reasoning_id

    def _emit_reasoning_delta(
        self,
        channel: ReasoningChannel,
        delta: str,
    ) -> None:
        if not delta:
            return
        reasoning_id = self._ensure_reasoning_started()
        self._emit(
            AgentReasoningDelta(
                reasoning_id=reasoning_id,
                channel=channel,
                delta=delta,
            )
        )

    async def on_reasoning_stream(
        self,
        context: AgentHookContext,
        delta: str,
        channel: ReasoningChannel,
    ) -> None:
        if not delta or id(context) != self._reasoning_context_id:
            return
        if channel is ReasoningChannel.SUMMARY:
            self._reasoning_summary += delta
        else:
            self._reasoning_trace += delta
        if self._emit_deltas:
            self._emit_reasoning_delta(channel, delta)

    async def on_stream_end(
        self,
        context: AgentHookContext,
        *,
        resuming: bool,
    ) -> None:
        """Close one provider response item without deciding the final answer.

        Every response segment first completes as commentary. AgentSession
        upgrades the actual last segment to ``final_answer`` after all stop
        hooks and injections have settled.
        """

        if not self._emit_deltas:
            self._message_id = None
            self._message_text = ""
            return
        response_text = (
            context.response.content
            if context.response is not None
            and isinstance(context.response.content, str)
            else ""
        )
        text = response_text or self._message_text
        if not text:
            self._message_id = None
            self._message_text = ""
            return
        message_id = self._message_id or uuid4().hex
        self._emit(
            AgentMessageCompleted(
                message_id=message_id,
                text=text,
                phase=AgentMessagePhase.COMMENTARY,
            )
        )
        self._last_message_id = message_id
        self._last_message_text = text
        self._message_id = None
        self._message_text = ""

    async def on_model_response(self, context: AgentHookContext) -> None:
        usage = {
            str(key): value
            for key, value in context.usage.items()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        if usage:
            if self._usage_sink is not None:
                self._usage_sink(usage)
            self._emit(
                ModelUsageRecorded(
                    response_ordinal=context.response_ordinal,
                    usage=usage,
                )
            )
        if id(context) != self._reasoning_context_id or context.response is None:
            return

        response = context.response
        summary = response.reasoning_summary or self._reasoning_summary
        trace = response.reasoning_content or self._reasoning_trace
        if trace and trace == summary:
            trace = ""
        has_opaque_state = bool(response.provider_state or response.thinking_blocks)
        if not summary and not trace and not has_opaque_state:
            self._reasoning_context_id = None
            self._reasoning_started_at = None
            return

        if not self._emit_deltas:
            self._emit_reasoning_delta(ReasoningChannel.SUMMARY, summary)
            self._emit_reasoning_delta(ReasoningChannel.PROVIDER_TRACE, trace)
        else:
            if summary.startswith(self._reasoning_summary):
                self._emit_reasoning_delta(
                    ReasoningChannel.SUMMARY,
                    summary[len(self._reasoning_summary) :],
                )
            if trace.startswith(self._reasoning_trace):
                self._emit_reasoning_delta(
                    ReasoningChannel.PROVIDER_TRACE,
                    trace[len(self._reasoning_trace) :],
                )

        reasoning_id = self._ensure_reasoning_started()
        started_at = self._reasoning_started_at
        duration_ms = (
            max(0, int((monotonic() - started_at) * 1000))
            if started_at is not None
            else None
        )
        self._emit(
            AgentReasoningCompleted(
                reasoning_id=reasoning_id,
                summary_text=summary,
                trace_text=trace,
                availability=(
                    ReasoningAvailability.AVAILABLE
                    if summary or trace
                    else ReasoningAvailability.OPAQUE
                ),
                effort=self._reasoning_effort,
                duration_ms=duration_ms,
            )
        )
        self._reasoning_context_id = None
        self._reasoning_id = None
        self._reasoning_summary = ""
        self._reasoning_trace = ""
        self._reasoning_started_at = None

    def final_message_id(self, text: str) -> str:
        """Reuse the streamed item's ID only when its authoritative text matches."""

        if self._last_message_id is not None and self._last_message_text == text:
            return self._last_message_id
        return uuid4().hex

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for call in context.tool_calls:
            detail: str | None = None
            if self._tools is not None:
                tool = self._tools.get(call.name)
                if tool is not None:
                    try:
                        detail = tool.presentation_detail(call.arguments)
                    except Exception as exc:  # noqa: BLE001 - presentation boundary
                        logger.warning(
                            "Ignoring unsafe tool presentation detail ({})",
                            type(exc).__name__,
                        )
                        detail = ""
            if detail is None:
                detail = summarize_call(call.name, call.arguments)
            else:
                detail = _one_line_detail(detail)
            self._emit(
                ToolStarted(
                    call_id=call.id,
                    name=call.name,
                    detail=detail,
                    activity=describe_tool_activity(call.name, call.arguments),
                )
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        for call, result in zip(context.tool_calls, context.tool_results):
            is_error = _is_error_result(result)
            if call.name.lower() == "update_plan" and not is_error:
                try:
                    self._emit(parse_plan_update(call.arguments))
                except ValueError:
                    logger.warning(
                        "update_plan succeeded but its arguments could not be projected"
                    )
            self._emit(
                ToolCompleted(
                    call_id=call.id,
                    name=call.name,
                    is_error=is_error,
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
        max_iterations: int | None = None,
        permission_checker: Any | None = None,
        approval_callback: Any | None = None,
        injection_callback: Any | None = None,
        context_note_sink: Any | None = None,
        hooks_engine: Any | None = None,
        agent_context: tuple[str, str] | None = None,
        context_window_tokens: int | None = None,
        workspace: str | Path | None = None,
        streaming: bool = False,
        streaming_transport: bool | None = None,
        skill_runtime: SkillRuntime | None = None,
        execution_profile: Any | None = None,
        tool_filter: Any | None = None,
        closure_callback: Any | None = None,
        mcp_runtime: McpSessionRuntime | None = None,
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
        # Records mid-turn model-visible messages into canonical history
        # (model-visible means logged); None keeps the kernel host-agnostic.
        self._context_note_sink = context_note_sink
        # External-command hooks (C3). Fires SessionStart (once) + UserPromptSubmit
        # (each prompt) here, and PreToolUse/PostToolUse in the runner. None when
        # no hooks are configured, so the whole feature is dormant at zero cost.
        self._hooks_engine = hooks_engine
        self._session_started = False
        self._session_end_fired = False
        self._session_end_lock = asyncio.Lock()
        # When this session is a spawned sub-agent, (agent_id, agent_type) — its
        # lifecycle fires SubagentStart/SubagentStop instead of SessionStart/Stop.
        self._agent_context = agent_context
        # Context-window budget that arms the runner's compaction ladder
        # (prune → summarize → _snip_history). Left unset it stays dormant and a
        # long enough session overflows the model; resolving it from the
        # model catalog is what makes "long sessions don't crash" (P2 exit
        # criterion) true for every AgentSession frontend — exec, TUI, web.
        self._context_window_tokens = context_window_tokens or context_window_for(model)
        # The execution root is model-visible Turn context, not just an
        # implementation detail of file and shell tools. This keeps Skill
        # resource paths from being mistaken for the task workspace.
        self._workspace = (
            Path(workspace).expanduser().resolve(strict=False)
            if workspace is not None
            else None
        )
        # When on, assistant text streams out as AgentMessageDelta events
        # (terminated by the authoritative AgentMessage). Interactive
        # frontends enable this; headless NDJSON consumers leave it off.
        self._streaming = streaming
        # Provider streaming is also the liveness mechanism for long reasoning.
        # It can remain enabled when a headless client does not want token
        # deltas projected into its event stream.
        self._streaming_transport = (
            streaming if streaming_transport is None else streaming_transport
        )
        # Skills are resolved here, at the frontend-neutral turn boundary.
        # A turn holds one immutable catalog snapshot, so a concurrent file
        # change can only affect the next turn.
        self._skill_runtime = skill_runtime
        # Application-owned dynamic capability narrowing (for example Goal
        # tools that exist only during a Goal-associated Turn). It composes with the
        # Skill visibility snapshot and can never add registry capabilities.
        self._tool_filter = tool_filter
        # Optional application-owned clean-exit check. Goal-associated Turns use
        # this to ask the model for one final complete/blocked/continue decision;
        # ordinary Turns leave it unset.
        self._closure_callback = closure_callback
        # P1-5: compaction summaries → memory vault (compacted sessions stay
        # retrievable). Built once here so auto and manual compaction share it.
        self._compaction_summary_sink = self._make_compaction_summary_sink()
        self._mcp_runtime = mcp_runtime
        # Secret-free immutable selection used by persistence/frontends.
        self.execution_profile = execution_profile

        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._history: list[dict[str, Any]] = []
        self._seq = 0
        self._busy = False
        self._last_usage: dict[str, int] = {}
        # One meter for the whole conversation. The spec is rebuilt every
        # Turn, so a per-spec meter would lose its anchor at exactly the
        # boundary where an accurate one matters most: the first request of
        # a new Turn over a long history.
        self._token_meter = ProviderAnchoredTokenMeter()
        self._current_task: asyncio.Task | None = None
        self._active_turn_task: asyncio.Task | None = None
        self._submission_id: ContextVar[str | None] = ContextVar(
            f"deepcode_submission_{id(self)}", default=None
        )

    # -- event queue -------------------------------------------------------

    def _emit(self, msg) -> None:
        self._seq += 1
        self._events.put_nowait(
            Event(
                id=str(self._seq),
                msg=msg,
                submission_id=self._submission_id.get(),
            )
        )

    def _make_compaction_summary_sink(self):
        """P1-5: build the compaction → memory deposit callable (never raises).

        The sink runs the memory write on a daemon thread (non-blocking, like
        memory distillation) so compaction never stalls the turn. Fires the
        P1-3 canonical ``memory.compaction.deposited`` event on success.
        """

        def _deposit(summary: str, anchor: dict[str, Any] | None = None) -> None:
            import threading

            def _work() -> None:
                try:
                    from core.harness.memory import write_compaction_summary

                    write_compaction_summary(
                        self._workspace, summary, anchor
                    )
                    try:
                        from core.observability.events import emit_event

                        emit_event(
                            "memory.compaction.deposited",
                            session=(anchor or {}).get("session_key"),
                            chars=len(summary or ""),
                            phase=(anchor or {}).get("phase"),
                        )
                    except Exception:  # noqa: BLE001, S110
                        pass
                except Exception:  # noqa: BLE001 - memory work never breaks turns
                    logger.debug("compaction summary deposit failed", exc_info=True)

            try:
                thread = threading.Thread(
                    target=_work,
                    name="compaction-memory",
                    daemon=True,
                )
                thread.start()
            except Exception:  # noqa: BLE001, S110
                pass

        return _deposit

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
        submission = Submission(id=uuid4().hex, op=op)
        async for event in self.run_stream_envelope(submission):
            yield event

    async def run_stream_envelope(self, submission: Submission):
        """Submit one SQ envelope and stream through its terminal event."""

        task = asyncio.ensure_future(self.submit_envelope(submission))
        try:
            while True:
                event = await self.next_event()
                yield event
                if event.submission_id == submission.id and event.msg.type in (
                    "task_complete",
                    "shutdown_complete",
                ):
                    break
        except BaseException:
            # Cancellation of the stream is the Turn interrupt boundary. Do
            # not keep awaiting a provider/tool submission that the caller has
            # explicitly abandoned; propagate cancellation into the active
            # task and wait only for its cleanup.
            if task.cancelling() == 0:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        else:
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

    @property
    def last_usage(self) -> dict[str, int]:
        """Usage observed so far in the current or most recent Turn."""

        return dict(self._last_usage)

    def _record_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            self._last_usage[key] = self._last_usage.get(key, 0) + value

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        """Replace the conversation history (session resume).

        ``messages`` are chat-format dicts (``{"role", "content"}``); the
        system prompt is prepended per turn, so it must not be included.
        """
        self._history = [dict(m) for m in messages]

    def _sync_environment_context(self) -> None:
        """Keep one durable environment slot at the front of history.

        Rewritten only when cwd, shell, or calendar date actually change so
        later turns stay append-only. Missing from a resumed excerpt, it is
        inserted once at index 0.
        """
        if self._workspace is None:
            return
        current = EnvironmentContext.for_workspace(self._workspace)
        for index, message in enumerate(self._history):
            if EnvironmentContext.is_history_message(message):
                if not current.matches_message(message):
                    self._history[index] = current.message()
                return
        self._history.insert(0, current.message())

    async def compact(self) -> dict[str, Any]:
        """Manually summarize older history (the `/compact` command, per dsh).

        Operates on the RESIDENT model context only — the canonical Session
        log is append-only and stays untouched, exactly like `/clear`'s
        ``load_history([])``. Raises :class:`RuntimeError` with a stable
        message when a Turn is active (the runner owns the history while it
        runs) or when there is nothing worth compacting; on success the
        report carries what changed so the caller can show it.
        """
        task = self._active_turn_task or self._current_task
        if task is not None and not task.done():
            raise RuntimeError("Compaction is unavailable while a Turn is active.")
        spec = AgentRunSpec(
            initial_messages=[],
            tools=self._tools,
            model=self._model,
            max_iterations=1,
            max_tool_result_chars=_DEFAULT_MAX_TOOL_RESULT_CHARS,
            context_window_tokens=self._context_window_tokens,
token_meter=self._token_meter,
            compaction_summary_sink=self._compaction_summary_sink,
        )
        before = list(self._history)
        compacted, reason = await self._runner.compact_history(spec, before)
        if compacted is None:
            raise RuntimeError(reason)
        self._history = compacted
        return {
            "replaced_messages": len(before) - len(compacted),
            "messages_before": len(before),
            "messages_after": len(compacted),
            "chars_before": sum(len(str(m.get("content", ""))) for m in before),
            "chars_after": sum(len(str(m.get("content", ""))) for m in compacted),
        }

    # -- submission handling ----------------------------------------------

    async def submit(self, op: Op) -> None:
        """Process one submission, emitting events onto the queue."""
        if isinstance(op, UserInput):
            await self._run_user_input(op)
        elif isinstance(op, Interrupt):
            task = self._active_turn_task or self._current_task
            if task is not None and not task.done() and task.cancelling() == 0:
                task.cancel()
        elif isinstance(op, Shutdown):
            # SessionEnd fires exactly once, here — at the real session
            # termination boundary — never per turn. Per-turn notifications
            # are the Stop event's job (see _EVENTS_WITHOUT_MATCHER).
            await self._run_end_hook(reason="other")
            self._emit(ShutdownComplete())
        else:  # pragma: no cover - exhaustive guard
            self._emit(ErrorEvent(message=f"unknown op: {op!r}"))

    async def submit_envelope(self, submission: Submission) -> None:
        token = self._submission_id.set(submission.id)
        try:
            await self.submit(submission.op)
        finally:
            self._submission_id.reset(token)

    async def aclose(self) -> None:
        """Cancel active work and release session-owned tool processes."""

        task = self._active_turn_task or self._current_task
        if task is not None and not task.done():
            if task.cancelling() == 0:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._run_end_hook(reason="other")
        control = getattr(self, "_agent_control", None)
        if control is not None:
            await control.close()
        if self._mcp_runtime is not None:
            await self._mcp_runtime.aclose()
        await self._tools.aclose()

    async def _cancel_turn_subagents(self) -> None:
        """Stop delegated work without letting repeated Stop interrupt teardown."""

        control = getattr(self, "_agent_control", None)
        if control is not None:
            cleanup_task = asyncio.create_task(control.cancel_running())
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    break
                except asyncio.CancelledError:
                    # Stop can race with the short finishing phase. It may
                    # cancel the parent Turn, but it must not cancel child
                    # teardown or suppress the terminal event. Further Stop
                    # requests see the parent task's cancelling state and do
                    # not inject another cancellation.
                    if cleanup_task.cancelled():
                        raise
                    if cleanup_task.done():
                        cleanup_task.result()
                        break

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

    async def _run_end_hook(self, reason: str = "other") -> None:
        """Run SessionEnd hooks when the session itself terminates.

        Notification-only: a failure is logged and never crashes the
        shutdown. Fired exactly once from ``submit(Shutdown)`` or the actual
        resource teardown path, whichever comes first. ``other`` is the
        compatible exit reason for a DeepCode runtime shutdown; per-turn
        notifications belong to the Stop event, not SessionEnd.
        """
        async with self._session_end_lock:
            if self._session_end_fired:
                return
            self._session_end_fired = True
            # Spawned agents use their dedicated SubagentStop lifecycle.
            if self._agent_context is not None:
                return
            engine = self._hooks_engine
            if engine is None or not engine.has_event("SessionEnd"):
                return
            try:
                await engine.run_session_end(reason=reason)
            except Exception:  # noqa: BLE001 - hooks never crash a shutdown
                logger.exception("session end hook failed")

    async def _run_prompt_hooks(
        self, text: str, hook_contexts: list[str]
    ) -> str | None:
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

    async def _run_user_input(self, op: UserInput | str) -> None:
        """Resolve one immutable turn and run it through the shared kernel.

        ``str`` remains accepted for compatibility with older direct tests and
        integrations; protocol callers should send :class:`UserInput`.
        """
        user_input = op if isinstance(op, UserInput) else UserInput(text=op)
        text = user_input.text
        if self._busy:
            self._emit(ErrorEvent(message="a turn is already in progress"))
            self._emit(TaskComplete(final_text=None, stop_reason="busy"))
            return
        self._busy = True
        self._last_usage = {}
        self._active_turn_task = asyncio.current_task()
        skill_context: SkillTurnContext | None = None
        skill_token = None
        terminal: TaskComplete | None = None
        try:
            if self._mcp_runtime is not None:
                try:
                    await self._mcp_runtime.ensure_started()
                except McpStartupError as exc:
                    self._emit(ErrorEvent(message=str(exc)))
                    terminal = TaskComplete(
                        final_text=None,
                        stop_reason="mcp_startup_failed",
                    )
                    return
            if self._skill_runtime is not None:
                try:
                    skill_context, skill_token = self._skill_runtime.begin_turn(
                        text,
                        user_input.skills,
                        available_tools=tuple(self._tools.tool_names),
                        available_mcp_servers=(
                            self._mcp_runtime.skill_capabilities
                            if self._mcp_runtime is not None
                            else None
                        ),
                    )
                except SkillError as exc:
                    self._emit(
                        SkillLoadFailed(
                            message=str(exc),
                            skill_id=(
                                user_input.skills[0].skill_id
                                if user_input.skills
                                else None
                            ),
                        )
                    )
                    self._emit(ErrorEvent(message=str(exc)))
                    terminal = TaskComplete(
                        final_text=None,
                        stop_reason="invalid_skill",
                    )
                    return

            invocations = (
                skill_context.snapshot.invocations if skill_context is not None else ()
            )
            self._emit(TurnStarted(skill_invocations=invocations))
            for invocation in invocations:
                self._emit(SkillLoaded(invocation=invocation))
            if skill_context is not None:
                # Progressive-disclosure loads happen during tool execution and
                # join the same per-turn invocation ledger.
                skill_context.on_invocation = lambda invocation: self._emit(
                    SkillLoaded(invocation=invocation)
                )
                skill_context.on_failure = lambda message, skill_id: self._emit(
                    SkillLoadFailed(message=message, skill_id=skill_id)
                )
            terminal = await self._execute_turn(text, skill_context)
        except asyncio.CancelledError:
            terminal = TaskComplete(final_text=None, stop_reason="interrupted")
        finally:
            if skill_token is not None and self._skill_runtime is not None:
                try:
                    self._skill_runtime.end_turn(skill_token)
                except Exception as exc:  # noqa: BLE001 - best-effort teardown
                    logger.warning(
                        "Skill turn cleanup failed ({})",
                        type(exc).__name__,
                    )
            await self._cancel_turn_subagents()
            self._busy = False
            self._current_task = None
            self._active_turn_task = None
            if terminal is not None:
                self._emit(terminal)
            # SessionEnd is NOT fired here: this finally block runs after
            # every turn, and SessionEnd must fire exactly once at session
            # termination (submit(Shutdown)), not per turn. Per-turn
            # notifications are the Stop event's responsibility.

    async def _execute_turn(
        self,
        text: str,
        skill_context: SkillTurnContext | None,
    ) -> TaskComplete:
        # External-command hooks (C3): SessionStart (once) + UserPromptSubmit
        # (every prompt). UserPromptSubmit may block the turn outright or inject
        # context; SessionStart injects session context. Injected context rides
        # as system messages ahead of history so the model reads it this turn.
        hook_contexts: list[str] = []
        if self._hooks_engine is not None:
            try:
                blocked = await self._run_prompt_hooks(text, hook_contexts)
            except asyncio.CancelledError:
                return TaskComplete(
                    final_text=None,
                    stop_reason="interrupted",
                )
            if blocked is not None:
                self._sync_environment_context()
                self._history.append({"role": "user", "content": text})
                return TaskComplete(
                    final_text=blocked,
                    stop_reason="blocked_by_hook",
                )

        self._sync_environment_context()
        self._history.append({"role": "user", "content": text})

        initial: list[dict[str, Any]] = []
        if self._system_prompt:
            initial.append({"role": "system", "content": self._system_prompt})
        for ctx in hook_contexts:
            initial.append({"role": "system", "content": ctx})
        initial.extend(self._history)

        turn_context_messages: list[dict[str, Any]] = []
        if self._skill_runtime is not None and skill_context is not None:
            turn_context_messages.extend(
                self._skill_runtime.prompt_bundle(
                    skill_context,
                    context_window_tokens=self._context_window_tokens,
                ).messages()
            )
        if self._mcp_runtime is not None:
            mcp_context = self._mcp_runtime.instruction_context()
            if mcp_context:
                turn_context_messages.append({"role": "system", "content": mcp_context})

        pre_tool_hook = post_tool_hook = permission_request_hook = stop_hook = None
        pre_compact_hook = post_compact_hook = None
        if self._hooks_engine is not None:
            if self._hooks_engine.has_event("PreToolUse"):
                pre_tool_hook = self._hooks_engine.run_pre_tool_use
            if self._hooks_engine.has_event("PostToolUse"):
                post_tool_hook = self._hooks_engine.run_post_tool_use
            if self._hooks_engine.has_event("PermissionRequest"):
                permission_request_hook = self._hooks_engine.run_permission_request
            if self._hooks_engine.has_event("PreCompact"):
                pre_compact_hook = self._hooks_engine.run_pre_compact
            if self._hooks_engine.has_event("PostCompact"):
                post_compact_hook = self._hooks_engine.run_post_compact
            if self._agent_context is not None:
                if self._hooks_engine.has_event("SubagentStop"):
                    agent_id, agent_type = self._agent_context
                    stop_hook = partial(
                        self._hooks_engine.run_subagent_stop, agent_id, agent_type
                    )
            elif self._hooks_engine.has_event("Stop"):
                stop_hook = self._hooks_engine.run_stop

        if stop_hook is not None or self._closure_callback is not None:
            external_stop_hook = stop_hook

            async def combined_stop_hook(stop_hook_active: bool) -> str | None:
                reasons: list[str] = []
                if external_stop_hook is not None:
                    try:
                        external_outcome = await external_stop_hook(stop_hook_active)
                    except Exception:
                        logger.exception("external stop hook failed")
                    else:
                        reason = _stop_continuation_reason(external_outcome)
                        if reason is not None:
                            reasons.append(reason)
                if self._closure_callback is not None:
                    try:
                        reason = self._closure_callback(stop_hook_active)
                    except Exception:
                        logger.exception("turn closure callback failed")
                    else:
                        if isinstance(reason, str) and reason.strip():
                            reasons.append(reason.strip())
                return "\n\n".join(reasons) or None

            stop_hook = combined_stop_hook

        event_hook = _EventEmittingHook(
            self._emit,
            streaming=self._streaming_transport,
            emit_deltas=self._streaming,
            usage_sink=self._record_usage,
            reasoning_effort=(
                getattr(self.execution_profile, "reasoning_effort", None)
                if self.execution_profile is not None
                else None
            ),
            tools=self._tools,
        )

        def visible_tool_names() -> tuple[str, ...] | None:
            names = tuple(self._tools.tool_names)
            if self._skill_runtime is not None:
                skill_names = self._skill_runtime.visible_tool_names(names)
                if skill_names is not None:
                    names = tuple(skill_names)
            if self._tool_filter is not None:
                value = self._tool_filter(names)
                if value is not None:
                    names = tuple(str(name) for name in value)
            return names

        # P1-4: default low temperature for the tool loop (reproducible
        # executions); an explicit execution-profile temperature (creative
        # subtasks) wins. 0.0 is a legitimate explicit choice, so compare
        # against None rather than truthiness.
        _profile_temperature = (
            getattr(self.execution_profile, "temperature", None)
            if self.execution_profile is not None
            else None
        )
        spec = AgentRunSpec(
            initial_messages=initial,
            tools=self._tools,
            model=self._model,
            max_iterations=self._max_iterations,
            max_tool_result_chars=_DEFAULT_MAX_TOOL_RESULT_CHARS,
token_meter=self._token_meter,
            temperature=(
                _profile_temperature
                if _profile_temperature is not None
                else _DEFAULT_TOOL_LOOP_TEMPERATURE
            ),
            transient_context_messages=tuple(turn_context_messages),
            workspace=self._workspace,
            context_window_tokens=self._context_window_tokens,
            hook=event_hook,
            permission_checker=self._permission_checker,
            approval_callback=self._approval_callback,
            injection_callback=self._injection_callback,
            context_note_sink=self._context_note_sink,
            pre_tool_hook=pre_tool_hook,
            post_tool_hook=post_tool_hook,
            permission_request_hook=permission_request_hook,
            stop_hook=stop_hook,
            pre_compact_hook=pre_compact_hook,
            post_compact_hook=post_compact_hook,
            tool_filter=(
                visible_tool_names
                if self._skill_runtime is not None or self._tool_filter is not None
                else None
            ),
            compaction_summary_sink=self._compaction_summary_sink,
        )

        try:
            self._current_task = asyncio.ensure_future(self._runner.run(spec))
            result = await self._current_task
        except asyncio.CancelledError:
            return TaskComplete(final_text=None, stop_reason="interrupted")
        except Exception as exc:  # noqa: BLE001
            # The runner should return errors as data, but a truly unexpected
            # exception must still terminate the turn — otherwise a consumer
            # blocked on the next event (run_stream) would hang forever. Always
            # close the turn with an error + task_complete.
            self._emit(ErrorEvent(message=f"{type(exc).__name__}: {exc}"))
            return TaskComplete(final_text=None, stop_reason="error")
        finally:
            self._current_task = None

        # Persist the turn's messages (minus the system prompt) as history.
        self._history = [m for m in result.messages if m.get("role") != "system"]
        self._last_usage = dict(result.usage)

        if result.final_content:
            self._emit(
                AgentMessage(
                    text=result.final_content,
                    message_id=event_hook.final_message_id(result.final_content),
                    phase=AgentMessagePhase.FINAL_ANSWER,
                )
            )
        if result.error and result.stop_reason in ("error", "empty_final_response"):
            self._emit(ErrorEvent(message=result.error))
        return TaskComplete(
            final_text=result.final_content,
            stop_reason=result.stop_reason,
        )
