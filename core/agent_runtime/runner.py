"""Shared execution loop for tool-using agents.

Ported from ``nanobot.agent.runner`` and adapted for DeepCode:

- All ``nanobot.*`` imports rewritten to ``core.*``.
- The single ``render_template`` call for the max-iterations message is
  replaced with an inline string so that no Jinja2 templates folder is
  required.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.agent_runtime.compaction import (
    COMPACT_TRIGGER_FRACTION as _COMPACT_TRIGGER_FRACTION,
)
from core.agent_runtime.compaction import (
    DEFAULT_COMPACTION_STRATEGY,
    CompactionStrategy,
)
from core.agent_runtime.compaction import (
    SUMMARIZATION_PROMPT as _SUMMARIZATION_PROMPT,
)
from core.agent_runtime.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    find_legal_message_start,
    history_signature,
    maybe_persist_tool_result,
    truncate_text,
)
from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.injections import (
    GoalObjectiveUpdated,
    SubagentMessage,
    UserSteer,
    runtime_input_to_provider_message,
)
from core.agent_runtime.pruner import ToolResultPruner
from core.agent_runtime.repeat_guard import (
    DEFAULT_THRESHOLDS as DEFAULT_REPEAT_THRESHOLDS,
)
from core.agent_runtime.repeat_guard import (
    RepeatCallTracker,
)
from core.agent_runtime.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_finalization_retry_message,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
    repeated_external_lookup_error,
)
from core.agent_runtime.token_meter import (
    DEFAULT_TOKEN_METER_FACTORY,
    TokenMeter,
)
from core.agent_runtime.tools.base import ToolResult
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    is_context_window_error,
)
from core.providers.timeouts import (
    resolve_request_timeout_s,
    resolve_stream_max_runtime_s,
)
from core.reasoning import ReasoningChannel

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_DEFAULT_MAX_ITERATIONS_MESSAGE = (
    "I reached the maximum number of tool call iterations ({max_iterations}) "
    "without completing the task. You can try breaking the task into smaller steps."
)
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_SNIP_SAFETY_BUFFER = 1024

# Summarization-based compaction (C4a). When the prompt nears the context
# budget, a model call condenses the conversation into a handoff summary that
# replaces old turns — semantic compaction, unlike the drop-based _snip_history
# fallback. The compacted history is returned and persisted by the session, so
# it survives across turns and is not re-summarized every step.
_BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"

# PreCompact checkpoint re-injection (bounded, provider-safe). A PreCompact
# hook may attach ``additional_contexts`` that must survive a successful
# compaction so the post-compaction model can restore working context. The
# re-injection is a plain ``role: user`` message (provider-agnostic) carrying a
# clearly delimited prefix, with hard limits per context and in total — a
# runaway hook can never blow the post-compaction window back open.
_PRECOMPACT_CHECKPOINT_PREFIX = "[PreCompact checkpoint]"
_PRECOMPACT_CONTEXT_LIMIT = 2000  # chars per additional context
_PRECOMPACT_TOTAL_LIMIT = 8000  # chars for the whole checkpoint block


def _build_precompact_checkpoint(
    contexts: list[str],
    *,
    total_limit: int = _PRECOMPACT_TOTAL_LIMIT,
) -> str | None:
    """Bounded, delimited representation of PreCompact hook context.

    Each context is stripped, truncated to ``_PRECOMPACT_CONTEXT_LIMIT`` chars
    and the combined block capped at ``_PRECOMPACT_TOTAL_LIMIT``. Returns
    ``None`` when nothing survives (empty input or all contexts blank).
    """
    prefix = _PRECOMPACT_CHECKPOINT_PREFIX + "\n"
    total_limit = min(max(total_limit, 0), _PRECOMPACT_TOTAL_LIMIT)
    content_limit = total_limit - len(prefix)
    if not contexts or content_limit <= 0:
        return None
    parts: list[str] = []
    used = 0
    for ctx in contexts:
        text = (ctx or "").strip()
        if not text:
            continue
        text = text[:_PRECOMPACT_CONTEXT_LIMIT]
        room = content_limit - used
        if room <= 0:
            break
        parts.append(text[:room])
        used += min(len(text), room) + 1  # +1 for the newline separator
    if not parts:
        return None
    return prefix + "\n".join(parts)


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int | None
    max_tool_result_chars: int
    # Ephemeral user-priority context included in every provider request for
    # this run, but never added to persisted history or compaction summaries.
    transient_context_messages: tuple[dict[str, Any], ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    # Advisory repeat-call reminders (see core.agent_runtime.repeat_guard):
    # run lengths of identical consecutive tool calls that earn an escalating
    # reminder instead of a hard stop. ``None`` disables the guard.
    repeat_call_thresholds: tuple[int, ...] | None = DEFAULT_REPEAT_THRESHOLDS
    # Model-visible means logged (the dsh session-log rule): mid-turn messages
    # the runner itself adds to the PERSISTED model history — injected
    # sub-agent results, Goal updates, repeat-call reminders, length-recovery
    # prompts, stop-hook continuations — reach the model but are invisible to
    # the host's canonical persistence, so a resumed Session would silently
    # rebuild a DIFFERENT history than the model actually saw. Per-request
    # transients (context messages, the finalization-retry prompt, the
    # compaction instruction) are exempt: they never enter persisted history,
    # so resident and canonical state already agree without a note.
    # This optional callable ``(content: str, source: str) -> None`` lets the
    # host record each one as it happens. A sink failure is logged and
    # swallowed: persistence trouble must not abort the run it documents.
    context_note_sink: Any | None = None
    workspace: Path | None = None
    session_key: str | None = None
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None
    should_stop_callback: Any | None = None
    # Optional dynamic restriction applied after the registry and before model
    # exposure/execution. It may narrow the existing registry but can never add
    # a tool or bypass the normal permission checker.
    tool_filter: Any | None = None
    # Permission seam (P1 security base). A callable
    # ``(tool_name, arguments) -> (decision, reason)`` where ``decision`` is
    # one of "allow"/"ask"/"deny" (str or enum with a ``.value``). Called
    # before each tool executes. "deny" and unresolved "ask" turn into an
    # errors-as-data tool result fed back to the model — never a crash.
    # ``ask`` is resolved by ``approval_callback`` if provided, else denied.
    permission_checker: Any | None = None
    approval_callback: Any | None = None
    # External-command hook seams (C3). Optional async callables invoked around
    # each tool call, mirroring ``permission_checker``:
    #   ``pre_tool_hook(tool_name, arguments)`` -> outcome with ``.block`` /
    #     ``.block_reason`` / ``.additional_contexts`` / ``.updated_input``.
    #   ``post_tool_hook(tool_name, arguments, result)`` -> outcome with
    #     ``.block`` / ``.block_reason`` / ``.additional_contexts``.
    # A blocking PreToolUse becomes an errors-as-data result (the tool never
    # runs); ``updated_input`` rewrites the call; contexts are appended to the
    # result the model reads. Absent (None) means no hooks — zero cost.
    pre_tool_hook: Any | None = None
    post_tool_hook: Any | None = None
    # PermissionRequest hook (C3.1): fires in the approval path when a tool
    # needs confirmation ("ask"), before the human approver. A hook verdict
    # (``.decision`` "allow"/"deny" + ``.message``) short-circuits the prompt;
    # no verdict falls through to ``approval_callback``.
    permission_request_hook: Any | None = None
    # Compaction hooks (C4a). ``pre_compact_hook(trigger)`` fires before a
    # summarization pass — a ``.block`` outcome skips compaction this turn;
    # ``post_compact_hook(trigger)`` fires after. Both optional; ``trigger`` is
    # "auto" (only automatic compaction exists so far).
    pre_compact_hook: Any | None = None
    post_compact_hook: Any | None = None
    # Model-free pruning pass (dsh's tool-result pruner). Under context
    # pressure, oversized tool results are middle-pruned in the PERSISTED
    # history before any summarization round-trip is considered; when the
    # free pass alone clears pressure, no model call happens. ``None``
    # disables pruning.
    tool_result_pruner: ToolResultPruner | None = field(
        default_factory=ToolResultPruner
    )
    compaction_strategy: CompactionStrategy = DEFAULT_COMPACTION_STRATEGY
    # Per-run: the anchored meter carries this conversation's last
    # reported prompt size, which must not leak into another Session.
    token_meter: TokenMeter = field(default_factory=DEFAULT_TOKEN_METER_FACTORY)
    # Stop hook (C3.1): fires when the turn would end cleanly. If it asks to
    # continue (``.block`` with ``.block_reason``), the reason is injected as a
    # follow-up prompt and the loop keeps going. ``stop_hook_active`` is passed
    # so a well-behaved hook stops blocking after its first continuation.
    stop_hook: Any | None = None
    # P1-5 (GenAI lesson 15): compaction-as-memory. Called with the handoff
    # summary + anchor metadata (session key, phase, timestamp, replaced
    # message count) after a compaction successfully shrinks the history, so
    # the host can deposit the summary into the memory vault — compressed
    # sessions stay retrievable instead of vanishing. Must never raise; a
    # failing sink is logged and swallowed.
    compaction_summary_sink: Any | None = None

    def allowed_tool_names(self) -> frozenset[str] | None:
        if self.tool_filter is None:
            return None
        value = self.tool_filter()
        if value is None:
            return None
        return frozenset(str(name) for name in value)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = self.tools.get_definitions()
        allowed = self.allowed_tool_names()
        if allowed is None:
            return definitions
        return [
            schema
            for schema in definitions
            if ToolRegistry._schema_name(schema) in allowed
        ]


@dataclass(slots=True)
class _SamplingLimit:
    """Optional caller-selected sampling limit.

    Normal agent turns are unbounded and stop on model completion,
    cancellation, or runtime failure. Tests and explicit CLI overrides may
    still set a positive limit without spreading ``None`` checks throughout
    the execution loop.
    """

    maximum: int | None
    remaining: int | None = field(init=False)

    def __post_init__(self) -> None:
        if self.maximum is not None and self.maximum < 1:
            raise ValueError("max_iterations must be positive when provided")
        self.remaining = self.maximum

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    def consume(self) -> None:
        if self.remaining is not None:
            self.remaining -= 1

    def reset(self) -> None:
        self.remaining = self.maximum


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of a shared agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)
    had_injections: bool = False


class AgentRunner:
    """Run a tool-capable LLM loop without product-layer concerns."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        # The signature of a history whose automatic compaction was already
        # refused. Under sustained pressure the gate fires on every step, and
        # a history with nothing older to replace — one long turn — gets the
        # same summary refused every time by the convergence rule. Paying a
        # model round-trip per step to relearn that is pure waste; the memo
        # clears itself the moment the history actually changes.
        self._refused_compaction: tuple[tuple[str, int], ...] | None = None

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    item
                    if isinstance(item, dict)
                    else {"type": "text", "text": str(item)}
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    @classmethod
    def _append_injected_messages(
        cls,
        messages: list[dict[str, Any]],
        injections: list[dict[str, Any]],
    ) -> None:
        for injection in injections:
            if (
                messages
                and injection.get("role") == "user"
                and messages[-1].get("role") == "user"
            ):
                merged = dict(messages[-1])
                merged["content"] = cls._merge_message_content(
                    merged.get("content"),
                    injection.get("content"),
                )
                messages[-1] = merged
                continue
            messages.append(injection)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        assistant_message: dict[str, Any] | None,
        *,
        phase: str = "after error",
        iteration: int | None = None,
        close_if_empty: bool = False,
    ) -> bool:
        injections = await self._drain_injections(
            spec,
            close_if_empty=close_if_empty,
        )
        if not injections:
            return False
        if assistant_message is not None:
            messages.append(assistant_message)
            if iteration is not None:
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                    },
                )
        self._append_injected_messages(messages, injections)
        logger.info(
            "Injected {} follow-up message(s) {}",
            len(injections),
            phase,
        )
        return True

    @staticmethod
    def _note_context(spec: AgentRunSpec, content: str, source: str) -> None:
        """Report one mid-turn model-visible message to the host's sink."""
        if spec.context_note_sink is None:
            return
        try:
            spec.context_note_sink(content, source)
        except Exception:
            # Persistence trouble must not abort the run it documents.
            logger.exception("context_note_sink failed (source={})", source)

    async def _drain_injections(
        self,
        spec: AgentRunSpec,
        *,
        close_if_empty: bool = False,
    ) -> list[dict[str, Any]]:
        if spec.injection_callback is None:
            return []
        signature = inspect.signature(spec.injection_callback)
        parameters = signature.parameters.values()
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        )
        kwargs: dict[str, Any] = {}
        if accepts_kwargs or "limit" in signature.parameters:
            kwargs["limit"] = None
        if accepts_kwargs or "close_if_empty" in signature.parameters:
            kwargs["close_if_empty"] = close_if_empty
        items = await spec.injection_callback(**kwargs)
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            message = runtime_input_to_provider_message(item)
            # Model-visible means logged — for every drained input nothing
            # else persists. Steering is appended to the canonical Session by
            # the service that accepted it; Goal updates, sub-agent results,
            # and raw injections exist only in this run's memory until noted.
            if not isinstance(item, UserSteer):
                self._note_context(
                    spec,
                    str(message.get("content", "")),
                    self._injection_note_source(item),
                )
            if message.get("role") == "user" and "content" in message:
                injected_messages.append(message)
                continue
            text = str(message.get("content", ""))
            if text.strip():
                injected_messages.append({"role": "user", "content": text})
        return injected_messages

    @staticmethod
    def _injection_note_source(item: Any) -> str:
        if isinstance(item, SubagentMessage):
            return "subagent"
        if isinstance(item, GoalObjectiveUpdated):
            return "goal_update"
        return "injection"

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        messages = list(spec.initial_messages)
        final_content: str | None = None
        tools_used: list[str] = []
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        error: str | None = None
        stop_reason = "completed"
        tool_events: list[dict[str, str]] = []
        external_lookup_counts: dict[str, int] = {}
        repeat_tracker = (
            RepeatCallTracker(spec.repeat_call_thresholds)
            if spec.repeat_call_thresholds is not None
            else None
        )
        empty_content_retries = 0
        length_recovery_count = 0
        overflow_recoveries = 0
        had_injections = False
        stop_hook_active = False  # C3.1: set once a Stop hook has forced a continuation
        skip_stop_check_once = False
        response_ordinal = 0

        async def record_response(
            response: LLMResponse,
            context: AgentHookContext,
        ) -> dict[str, int]:
            """Account a provider response before any cancellable tool work."""

            nonlocal response_ordinal
            response_ordinal += 1
            raw_usage = self._usage_dict(response.usage)
            self._accumulate_usage(usage, raw_usage)
            # The provider just priced this exact history; that number is a
            # better anchor than any estimate of it (§9.1).
            spec.token_meter.observe(raw_usage, messages)
            context.response_ordinal = response_ordinal
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            await hook.on_model_response(context)
            return raw_usage

        initial_drained = await self._try_drain_injections(
            spec,
            messages,
            None,
            phase="before the first model call",
        )
        if initial_drained:
            had_injections = True

        iteration = 0
        sampling_limit = _SamplingLimit(spec.max_iterations)
        while True:
            if sampling_limit.exhausted:
                resumed = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    phase="at the iteration boundary",
                    close_if_empty=True,
                )
                if resumed:
                    had_injections = True
                    sampling_limit.reset()
                else:
                    stop_reason = "max_iterations"
                    template = (
                        spec.max_iterations_message or _DEFAULT_MAX_ITERATIONS_MESSAGE
                    )
                    final_content = template.format(max_iterations=spec.max_iterations)
                    self._append_final_message(messages, final_content)
                    break

            current_iteration = iteration
            iteration += 1
            sampling_limit.consume()

            check_stop = not skip_stop_check_once
            skip_stop_check_once = False
            if spec.should_stop_callback is not None and check_stop:
                try:
                    stop_requested = await spec.should_stop_callback()
                except Exception:
                    logger.exception(
                        "should_stop_callback failed for {}; continuing run",
                        spec.session_key or "default",
                    )
                    stop_requested = None
                if stop_requested:
                    stop_reason = "callback_stop"
                    logger.info(
                        "Run stopped by callback for {}: {}",
                        spec.session_key or "default",
                        stop_requested,
                    )
                    resumed = await self._try_drain_injections(
                        spec,
                        messages,
                        None,
                        phase="before callback stop",
                        close_if_empty=True,
                    )
                    if resumed:
                        had_injections = True
                        sampling_limit.reset()
                        skip_stop_check_once = True
                        continue
                    break

            # Summarization-based compaction (C4a): when the running history
            # nears the budget, replace old turns with a model summary. Persisted
            # in `messages` so later iterations (and turns) reuse it.
            async def record_compaction_response(response: LLMResponse) -> None:
                await record_response(
                    response,
                    AgentHookContext(
                        iteration=current_iteration,
                        messages=messages,
                    ),
                )

            messages = await self._maybe_compact(
                spec,
                messages,
                response_observer=record_compaction_response,
            )

            messages_for_model = self._request_view(
                spec, messages, iteration=current_iteration
            )
            context = AgentHookContext(
                iteration=current_iteration,
                messages=messages,
            )
            await hook.before_iteration(context)
            await hook.before_model_request(context)
            response = await self._request_model(
                spec, messages_for_model, hook, context
            )
            raw_usage = await record_response(response, context)

            if response.should_execute_tools:
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    reasoning_summary=response.reasoning_summary,
                    provider_state=response.provider_state,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                tools_used.extend(tc.name for tc in response.tool_calls)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": current_iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [
                            tc.to_openai_tool_call() for tc in response.tool_calls
                        ],
                    },
                )

                await hook.before_execute_tools(context)

                results, new_events, fatal_error = await self._execute_tools(
                    spec,
                    response.tool_calls,
                    external_lookup_counts,
                )
                tool_events.extend(new_events)
                context.tool_results = list(results)
                context.tool_events = list(new_events)
                completed_tool_results: list[dict[str, Any]] = []
                for tool_call, result in zip(response.tool_calls, results):
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": self._normalize_tool_result(
                            spec,
                            tool_call.id,
                            tool_call.name,
                            result,
                        ),
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)
                if repeat_tracker is not None:
                    # Observed at the result boundary so denied and failed
                    # calls count too — a model hammering a rejected call is
                    # exactly the loop worth interrupting. The reminder rides
                    # a user message AFTER the results, so the model reads
                    # what happened and then why it should change course.
                    reminders = [
                        reminder
                        for tc in response.tool_calls
                        if (reminder := repeat_tracker.observe(tc.name, tc.arguments))
                        is not None
                    ]
                    if reminders:
                        reminder_text = "\n\n".join(reminders)
                        messages.append(
                            {
                                "role": "user",
                                "content": reminder_text,
                            }
                        )
                        self._note_context(spec, reminder_text, "repeat_guard")
                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    should_continue = await self._try_drain_injections(
                        spec,
                        messages,
                        None,
                        phase="after tool error",
                        close_if_empty=True,
                    )
                    if should_continue:
                        had_injections = True
                        sampling_limit.reset()
                        stop_reason = "completed"
                        error = None
                        continue
                    break
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "tools_completed",
                        "iteration": current_iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": completed_tool_results,
                        "pending_tool_calls": [],
                    },
                )
                empty_content_retries = 0
                length_recovery_count = 0
                drained = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    phase="after tool execution",
                )
                if drained:
                    had_injections = True
                    sampling_limit.reset()
                await hook.after_iteration(context)
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason,
                    spec.session_key or "default",
                )

            clean = hook.finalize_content(context, response.content)
            if response.finish_reason != "error" and is_blank_text(clean):
                empty_content_retries += 1
                if empty_content_retries < _MAX_EMPTY_RETRIES:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        current_iteration,
                        spec.session_key or "default",
                        empty_content_retries,
                        _MAX_EMPTY_RETRIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    current_iteration,
                    spec.session_key or "default",
                    empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                await hook.before_model_request(context)
                response = await self._request_finalization_retry(
                    spec, messages_for_model
                )
                retry_usage = await record_response(response, context)
                raw_usage = self._merge_usage(raw_usage, retry_usage)
                context.usage = dict(raw_usage)
                clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        current_iteration,
                        spec.session_key or "default",
                        length_recovery_count,
                        _MAX_LENGTH_RECOVERIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=True)
                    messages.append(
                        build_assistant_message(
                            clean,
                            reasoning_content=response.reasoning_content,
                            reasoning_summary=response.reasoning_summary,
                            provider_state=response.provider_state,
                            thinking_blocks=response.thinking_blocks,
                        )
                    )
                    recovery_message = build_length_recovery_message()
                    messages.append(recovery_message)
                    # Model-visible means logged: this recovery prompt joins
                    # the persisted history, so canonical storage must see it.
                    self._note_context(
                        spec,
                        str(recovery_message.get("content", "")),
                        "length_recovery",
                    )
                    await hook.after_iteration(context)
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    reasoning_summary=response.reasoning_summary,
                    provider_state=response.provider_state,
                    thinking_blocks=response.thinking_blocks,
                )

            should_continue = await self._try_drain_injections(
                spec,
                messages,
                assistant_message,
                phase="after final response",
                iteration=current_iteration,
            )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                sampling_limit.reset()
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                if overflow_recoveries < 1 and is_context_window_error(
                    response, message=clean
                ):
                    overflow_recoveries += 1
                    logger.info(
                        "Context-window overflow on turn {} for {}; recovering once",
                        current_iteration,
                        spec.session_key or "default",
                    )
                    if spec.tool_result_pruner is not None:
                        pruned, _ = spec.tool_result_pruner.prune_messages(messages)
                        messages[:] = pruned
                    messages[:] = self._overflow_reduce(spec, messages)
                    sampling_limit.reset()
                    await hook.after_iteration(context)
                    continue
                final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    phase="after LLM error",
                    close_if_empty=True,
                )
                if should_continue:
                    had_injections = True
                    sampling_limit.reset()
                    stop_reason = "completed"
                    error = None
                    continue
                break
            if is_blank_text(clean):
                final_content = EMPTY_FINAL_RESPONSE_MESSAGE
                stop_reason = "empty_final_response"
                error = final_content
                self._append_final_message(messages, final_content)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    phase="after empty response",
                    close_if_empty=True,
                )
                if should_continue:
                    had_injections = True
                    sampling_limit.reset()
                    stop_reason = "completed"
                    error = None
                    continue
                break

            messages.append(
                assistant_message
                or build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    reasoning_summary=response.reasoning_summary,
                    provider_state=response.provider_state,
                    thinking_blocks=response.thinking_blocks,
                )
            )
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": current_iteration,
                    "model": spec.model,
                    "assistant_message": messages[-1],
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                },
            )
            final_content = clean
            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)
            # Stop hook (C3.1): a last chance to keep the turn going. If it asks
            # to continue, inject its reason as a follow-up and loop again. The
            # `stop_hook_active` lets a well-behaved hook stand down after one
            # continuation so a closure check cannot loop on itself.
            continuation = await self._run_stop_hook(spec, stop_hook_active)
            if continuation is not None:
                stop_hook_active = True
                had_injections = True
                self._append_injected_messages(
                    messages, [{"role": "user", "content": continuation}]
                )
                # Model-visible means logged: the continuation joins the
                # persisted history, so canonical storage must see it.
                self._note_context(spec, continuation, "stop_hook")
                await hook.after_iteration(context)
                continue

            resumed = await self._try_drain_injections(
                spec,
                messages,
                None,
                phase="at final close",
                close_if_empty=True,
            )
            if resumed:
                had_injections = True
                sampling_limit.reset()
                continue
            break

        return AgentRunResult(
            final_content=final_content,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=error,
            tool_events=tool_events,
            had_injections=had_injections,
        )

    def _build_request_kwargs(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "model": spec.model,
            "retry_mode": spec.provider_retry_mode,
            "on_retry_wait": spec.retry_wait_callback,
        }
        if spec.temperature is not None:
            kwargs["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens
        if spec.reasoning_effort is not None:
            kwargs["reasoning_effort"] = spec.reasoning_effort
        return kwargs

    def _request_view(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        iteration: int | None = None,
    ) -> list[dict[str, Any]]:
        """Compose the exact provider-visible message list from history.

        One composition shared by the routed model request and the compaction
        summarizer: the summarizer replays this view and appends only its
        instruction, so the auxiliary call is a genuine prefix of the routed
        request and the provider's prefix/KV cache is reused rather than
        invalidated (the dsh rule). Any governance failure degrades to a
        minimal repair instead of aborting the run.
        """
        try:
            view = self._drop_orphan_tool_results(messages)
            view = self._backfill_missing_tool_results(view)
            view = self._apply_tool_result_budget(spec, view)
            view = self._with_transient_context(
                view,
                spec.transient_context_messages,
            )
            view = self._snip_history(spec, view)
            view = self._drop_orphan_tool_results(view)
            return self._backfill_missing_tool_results(view)
        except Exception as exc:
            logger.warning(
                "Context governance failed on turn {} for {}: {}; applying minimal repair",
                iteration if iteration is not None else "?",
                spec.session_key or "default",
                exc,
            )
            try:
                view = self._drop_orphan_tool_results(messages)
                return self._backfill_missing_tool_results(view)
            except Exception:
                return messages

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
    ):
        """Dispatch one provider request over an already-composed view."""
        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=spec.tool_definitions(),
        )
        requested_streaming = hook.wants_streaming()
        stream_request = getattr(self.provider, "chat_stream_with_retry", None)
        streaming = requested_streaming and callable(stream_request)
        if requested_streaming and not streaming:
            logger.debug(
                "Provider {} has no streaming transport; using non-streaming request",
                type(self.provider).__name__,
            )
        if streaming:

            async def _stream(delta: str) -> None:
                await hook.on_stream(context, delta)

            async def _reasoning_stream(
                delta: str,
                channel: ReasoningChannel,
            ) -> None:
                await hook.on_reasoning_stream(context, delta, channel)

            coro = stream_request(
                **kwargs,
                on_content_delta=_stream,
                on_reasoning_delta=_reasoning_stream,
            )
        else:
            coro = self.provider.chat_with_retry(**kwargs)

        timeout_s = (
            resolve_stream_max_runtime_s(spec.llm_timeout_s)
            if streaming
            else resolve_request_timeout_s(spec.llm_timeout_s)
        )
        return await self._await_provider_response(
            coro,
            timeout_s=timeout_s,
            streaming=streaming,
        )

    @staticmethod
    async def _await_provider_response(
        request: Awaitable[LLMResponse],
        *,
        timeout_s: float | None,
        streaming: bool,
    ) -> LLMResponse:
        """Apply the correct deadline without conflating activity and runtime."""

        if timeout_s is None:
            return await request
        try:
            return await asyncio.wait_for(request, timeout=timeout_s)
        except TimeoutError:
            message = (
                "Error calling LLM: stream exceeded the configured maximum "
                f"runtime of {timeout_s:g}s"
                if streaming
                else f"Error calling LLM: timed out after {timeout_s:g}s"
            )
            return LLMResponse(
                content=message,
                finish_reason="error",
                error_kind="timeout",
            )

    async def _request_finalization_retry(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ):
        retry_messages = list(messages)
        retry_messages.append(build_finalization_retry_message())
        retry_messages = self._with_transient_context(
            retry_messages,
            spec.transient_context_messages,
        )
        retry_messages = self._snip_history(spec, retry_messages)
        kwargs = self._build_request_kwargs(spec, retry_messages, tools=None)
        return await self._await_provider_response(
            self.provider.chat_with_retry(**kwargs),
            timeout_s=resolve_request_timeout_s(spec.llm_timeout_s),
            streaming=False,
        )

    @staticmethod
    def _usage_dict(usage: dict[str, Any] | None) -> dict[str, int]:
        if not usage:
            return {}
        result: dict[str, int] = {}
        for key, value in usage.items():
            try:
                result[key] = int(value or 0)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
        for key, value in addition.items():
            target[key] = target.get(key, 0) + value

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        merged = dict(left)
        for key, value in right.items():
            merged[key] = merged.get(key, 0) + value
        return merged

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
        external_lookup_counts: dict[str, int],
    ) -> tuple[list[Any], list[dict[str, str]], BaseException | None]:
        batches = self._partition_tool_batches(spec, tool_calls)
        tool_results: list[tuple[Any, dict[str, str], BaseException | None]] = []
        for batch in batches:
            if spec.concurrent_tools and len(batch) > 1:
                tool_results.extend(
                    await asyncio.gather(
                        *(
                            self._run_tool(spec, tool_call, external_lookup_counts)
                            for tool_call in batch
                        )
                    )
                )
            else:
                for tool_call in batch:
                    tool_results.append(
                        await self._run_tool(spec, tool_call, external_lookup_counts)
                    )

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error

    async def _run_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        _HINT = "\n\n[Analyze the error above and try a different approach.]"
        allowed = spec.allowed_tool_names()
        if allowed is not None and tool_call.name not in allowed:
            message = (
                f"Error: tool {tool_call.name!r} is not allowed by the active "
                "Skill policy"
            )
            return (
                message + _HINT,
                {
                    "name": tool_call.name,
                    "status": "denied",
                    "detail": "blocked by active Skill policy",
                },
                None,
            )
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            if spec.fail_on_tool_error:
                return lookup_error + _HINT, event, RuntimeError(lookup_error)
            return lookup_error + _HINT, event, None

        # PreToolUse hook (C3). Fires before the permission gate — it may block
        # the call (errors-as-data, tool never runs), rewrite its arguments, or
        # attach context the model reads with the result.
        pre_contexts: list[str] = []
        if spec.pre_tool_hook is not None:
            pre = await self._call_tool_hook(
                spec.pre_tool_hook, tool_call.name, tool_call.arguments
            )
            if pre is not None:
                if getattr(pre, "block", False):
                    reason = getattr(pre, "block_reason", None) or "blocked by hook"
                    event = {
                        "name": tool_call.name,
                        "status": "denied",
                        "detail": reason.replace("\n", " ").strip()[:120],
                    }
                    return (
                        f"Error: blocked by PreToolUse hook: {reason}" + _HINT,
                        event,
                        None,
                    )
                updated = getattr(pre, "updated_input", None)
                if isinstance(updated, dict):
                    tool_call.arguments = updated
                pre_contexts = list(getattr(pre, "additional_contexts", None) or [])

        # Permission gate (P1 security base). Denials and unresolved asks
        # become errors-as-data results the model can read and react to,
        # never exceptions — a blocked tool must not abort the run.
        denial = await self._check_permission(spec, tool_call)
        if denial is not None:
            event = {
                "name": tool_call.name,
                "status": "denied",
                "detail": denial.replace("\n", " ").strip()[:120],
            }
            result = self._compose_hook_context(
                f"Error: permission denied: {denial}" + _HINT, pre_contexts
            )
            return result, event, None

        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            try:
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
            except Exception:
                pass
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            # Tool never ran (bad args) — surface pre-hook context, no PostToolUse.
            return (
                self._compose_hook_context(prep_error + _HINT, pre_contexts),
                event,
                RuntimeError(prep_error) if spec.fail_on_tool_error else None,
            )
        # Per-tool deadline (declared on Tool.timeout_s, enforced here so
        # every tool gets one implementation). ``asyncio.timeout`` gives the
        # attribution this needs for free: only OUR expired deadline becomes
        # ``TimeoutError`` with ``expired()`` true — an outer cancellation
        # passes through as ``CancelledError`` (re-raised below, unchanged),
        # and a ``TimeoutError`` the tool raised itself fails ``expired()``
        # and falls to the ordinary error path. Both mis-attributions would
        # otherwise blame this deadline for a stop it did not cause.
        declared = tool
        if declared is None:
            # ``spec.tools`` is a duck-typed seam (see ``prepare_call`` above):
            # a minimal registry without ``get`` simply declares no budgets.
            getter = getattr(spec.tools, "get", None)
            declared = getter(tool_call.name) if callable(getter) else None
        timeout_s = getattr(declared, "timeout_s", None)
        try:
            if timeout_s is not None:
                deadline = asyncio.timeout(timeout_s)
                try:
                    async with deadline:
                        if tool is not None:
                            result = await tool.execute(**params)
                        else:
                            result = await spec.tools.execute(tool_call.name, params)
                except TimeoutError:
                    if not deadline.expired():
                        raise
                    message = (
                        f"Error: tool call timed out after {timeout_s:g}s. "
                        "The call was cancelled; nothing further ran."
                    )
                    event = {
                        "name": tool_call.name,
                        "status": "timeout",
                        "detail": f"exceeded declared budget of {timeout_s:g}s",
                    }
                    return await self._finish_tool(
                        spec,
                        tool_call,
                        ToolResult(
                            message + _HINT,
                            is_error=True,
                            metadata={
                                "error_code": "TOOL_TIMEOUT",
                                "timeout_s": timeout_s,
                            },
                        ),
                        event,
                        None,
                        pre_contexts,
                    )
            elif tool is not None:
                result = await tool.execute(**params)
            else:
                result = await spec.tools.execute(tool_call.name, params)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            message = f"Error: {type(exc).__name__}: {exc}"
            return await self._finish_tool(
                spec,
                tool_call,
                message,
                event,
                exc if spec.fail_on_tool_error else None,
                pre_contexts,
            )

        if isinstance(result, str) and result.startswith("Error"):
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            return await self._finish_tool(
                spec,
                tool_call,
                result + _HINT,
                event,
                RuntimeError(result) if spec.fail_on_tool_error else None,
                pre_contexts,
            )

        if isinstance(result, ToolResult) and result.is_error:
            detail = str(result).replace("\n", " ").strip()[:120]
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": detail,
            }
            return await self._finish_tool(
                spec,
                tool_call,
                result,
                event,
                RuntimeError(str(result)) if spec.fail_on_tool_error else None,
                pre_contexts,
            )

        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        event = {"name": tool_call.name, "status": "ok", "detail": detail}
        return await self._finish_tool(
            spec, tool_call, result, event, None, pre_contexts
        )

    async def _finish_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        result: Any,
        event: dict[str, str],
        error: BaseException | None,
        pre_contexts: list[str],
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        """Apply PostToolUse (the tool ran) and fold hook context into the result."""
        contexts = list(pre_contexts)
        if spec.post_tool_hook is not None:
            post = await self._call_tool_hook(
                spec.post_tool_hook, tool_call.name, tool_call.arguments, result
            )
            if post is not None:
                contexts.extend(getattr(post, "additional_contexts", None) or [])
                if getattr(post, "block", False):
                    reason = getattr(post, "block_reason", None)
                    if reason:
                        contexts.append(f"PostToolUse hook feedback: {reason}")
        return self._compose_hook_context(result, contexts), event, error

    @staticmethod
    async def _call_tool_hook(hook: Any, *args: Any) -> Any:
        """Invoke a tool hook; a hook failure is logged and ignored, never fatal."""
        try:
            return await hook(*args)
        except Exception:
            logger.exception("tool hook failed")
            return None

    @staticmethod
    async def _run_stop_hook(spec: AgentRunSpec, stop_hook_active: bool) -> str | None:
        """Run the Stop hook; return a continuation prompt if it wants to keep
        going (block + reason), else ``None``. A failure is logged, never fatal."""
        if spec.stop_hook is None:
            return None
        try:
            outcome = await spec.stop_hook(stop_hook_active)
        except Exception:
            logger.exception("stop hook failed")
            return None
        if isinstance(outcome, str):
            reason = outcome.strip()
            return reason or None
        if outcome is not None and getattr(outcome, "block", False):
            reason = getattr(outcome, "block_reason", None)
            if reason and reason.strip():
                return reason
        return None

    @staticmethod
    def _compose_hook_context(result: Any, contexts: list[str]) -> Any:
        """Append accumulated hook context to a tool result the model will read."""
        if not contexts:
            return result
        joined = "\n\n".join(f"[hook] {c}" for c in contexts)
        content = f"{result}\n\n{joined}"
        if isinstance(result, ToolResult):
            return result.with_content(content)
        return content

    @staticmethod
    def _decision_value(decision: Any) -> str:
        return getattr(decision, "value", decision)

    async def _check_permission(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
    ) -> str | None:
        """Return a denial reason, or ``None`` if the call is permitted.

        ``allow`` → ``None``. ``deny`` → its reason. ``ask`` → resolved via
        ``approval_callback`` (approved → ``None``, rejected → reason);
        with no approval callback (headless runs) an ``ask`` is denied with
        an explanatory reason, so autonomy never silently escalates.
        """
        checker = spec.permission_checker
        if checker is None:
            return None
        try:
            outcome = checker(tool_call.name, tool_call.arguments)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            decision, reason = outcome
        except Exception:
            logger.exception(
                "permission_checker failed for {}; denying by default",
                tool_call.name,
            )
            return "permission check errored (fail-closed)"

        value = self._decision_value(decision)
        if value == "allow":
            return None
        if value == "deny":
            return reason or "denied by policy"

        # ask — a PermissionRequest hook may resolve it before the human is
        # prompted: a hook "deny" blocks, "allow" permits, no verdict falls
        # through to the approver below.
        if spec.permission_request_hook is not None:
            verdict = await self._call_tool_hook(
                spec.permission_request_hook, tool_call.name, tool_call.arguments
            )
            if verdict is not None:
                if getattr(verdict, "decision", None) == "deny":
                    return (
                        getattr(verdict, "message", None) or reason or "denied by hook"
                    )
                if getattr(verdict, "decision", None) == "allow":
                    return None

        approver = spec.approval_callback
        if approver is None:
            return (
                (reason or "requires confirmation")
                + " — no approver attached (non-interactive run), so this "
                "action is blocked. Choose a path/command inside the allowed "
                "workspace, or ask the user to approve it."
            )
        try:
            approved = approver(tool_call.name, tool_call.arguments, reason)
            if inspect.isawaitable(approved):
                approved = await approved
        except Exception:
            logger.exception("approval_callback failed for {}", tool_call.name)
            return "approval request errored (fail-closed)"
        if approved:
            return None
        return f"user rejected: {reason or 'action not approved'}"

    async def _emit_checkpoint(
        self,
        spec: AgentRunSpec,
        payload: dict[str, Any],
    ) -> None:
        callback = spec.checkpoint_callback
        if callback is not None:
            await callback(payload)

    @staticmethod
    def _append_final_message(
        messages: list[dict[str, Any]], content: str | None
    ) -> None:
        if not content:
            return
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    def _normalize_tool_result(
        self,
        spec: AgentRunSpec,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> Any:
        result = ensure_nonempty_tool_result(tool_name, result)
        try:
            content = maybe_persist_tool_result(
                spec.workspace,
                spec.session_key,
                tool_call_id,
                result,
                max_chars=spec.max_tool_result_chars,
            )
        except Exception as exc:
            logger.warning(
                "Tool result persist failed for {} in {}: {}; using raw result",
                tool_call_id,
                spec.session_key or "default",
                exc,
            )
            content = result
        if isinstance(content, str) and len(content) > spec.max_tool_result_chars:
            return truncate_text(content, spec.max_tool_result_chars)
        return content

    @staticmethod
    def _drop_orphan_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        declared: set[str] = set()
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared.add(str(tc["id"]))
            if role == "tool":
                tid = msg.get("tool_call_id")
                if tid and str(tid) not in declared:
                    if updated is None:
                        updated = [dict(m) for m in messages[:idx]]
                    continue
            if updated is not None:
                updated.append(dict(msg))

        if updated is None:
            return messages
        return updated

    @staticmethod
    def _backfill_missing_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        declared: list[tuple[int, str, str]] = []
        fulfilled: set[str] = set()
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        name = ""
                        func = tc.get("function")
                        if isinstance(func, dict):
                            name = func.get("name", "")
                        declared.append((idx, str(tc["id"]), name))
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid:
                    fulfilled.add(str(tid))

        missing = [
            (ai, cid, name) for ai, cid, name in declared if cid not in fulfilled
        ]
        if not missing:
            return messages

        updated = list(messages)
        offset = 0
        for assistant_idx, call_id, name in missing:
            insert_at = assistant_idx + 1 + offset
            while insert_at < len(updated) and updated[insert_at].get("role") == "tool":
                insert_at += 1
            updated.insert(
                insert_at,
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": _BACKFILL_CONTENT,
                },
            )
            offset += 1
        return updated

    def _apply_tool_result_budget(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updated = messages
        for idx, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            normalized = self._normalize_tool_result(
                spec,
                str(message.get("tool_call_id") or f"tool_{idx}"),
                str(message.get("name") or "tool"),
                message.get("content"),
            )
            if normalized != message.get("content"):
                if updated is messages:
                    updated = [dict(m) for m in messages]
                updated[idx]["content"] = normalized
        return updated

    def _context_budget(self, spec: AgentRunSpec) -> int | None:
        """Token budget for the model prompt (context window − output − buffer).

        ``None`` when unknown/non-positive. Shared by ``_snip_history`` and
        ``_maybe_compact`` so both agree on when the prompt is "too big".
        """
        if not spec.context_window_tokens:
            return None
        provider_max_tokens = getattr(
            getattr(self.provider, "generation", None), "max_tokens", 4096
        )
        max_output = (
            spec.max_tokens
            if isinstance(spec.max_tokens, int)
            else (provider_max_tokens if isinstance(provider_max_tokens, int) else 4096)
        )
        budget = spec.context_block_limit or (
            spec.context_window_tokens - max_output - _SNIP_SAFETY_BUFFER
        )
        return budget if budget > 0 else None

    async def _maybe_compact(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        response_observer: Callable[[LLMResponse], Awaitable[None]] | None = None,
    ) -> list[dict[str, Any]]:
        """Relieve context pressure with the cheapest sufficient measure.

        The ladder (dsh's ordering): pressure gate → model-free prune of
        oversized tool results → remeasure → only if still over pressure,
        a summarization round-trip that replaces old turns (C4a). Both
        effects are persisted in ``messages``; the drop-based
        ``_snip_history`` fallback still follows for anything left over.

        PreCompact/PostCompact hooks fire around it; a PreCompact ``block`` skips
        compaction this turn. Any failure returns the history unchanged so the
        drop-based fallback still keeps the prompt in-window. The compacted list
        is persisted by the session, so it survives across turns.
        """
        budget = self._context_budget(spec)
        if budget is None:
            return messages
        # Need a few real turns before a summary is worth a model round-trip.
        if sum(1 for m in messages if m.get("role") != "system") < 4:
            return messages
        trigger = int(budget * _COMPACT_TRIGGER_FRACTION)
        estimate = self._estimate_prompt(spec, messages)
        if estimate is None or estimate <= trigger:
            return messages

        # Model-free pass first (the dsh ladder): middle-prune oversized tool
        # results in the persisted history, remeasure, and skip the model
        # round-trip entirely when pruning alone clears pressure. A landed
        # prune is durable even when the summary phase later fails or is
        # blocked — that reduction is real and keeping it costs nothing.
        if spec.tool_result_pruner is not None:
            pruned, pruned_count = spec.tool_result_pruner.prune_messages(messages)
            if pruned_count:
                messages = pruned
                logger.info(
                    "Pruned {} oversized tool result(s) for {}",
                    pruned_count,
                    spec.session_key or "default",
                )
                estimate = self._estimate_prompt(spec, messages)
                if estimate is None or estimate <= trigger:
                    return messages

        pre_contexts: list[str] = []
        signature = history_signature(messages)
        if signature == self._refused_compaction:
            # Already tried on exactly this history and it did not shrink.
            return messages
        if spec.pre_compact_hook is not None:
            pre = await self._call_tool_hook(spec.pre_compact_hook, "auto")
            if pre is not None and getattr(pre, "block", False):
                return messages  # a PreCompact hook aborted compaction this turn
            pre_contexts = list(getattr(pre, "additional_contexts", None) or [])

        summary = await self._summarize(
            spec,
            messages,
            response_observer=response_observer,
        )
        if not summary:
            self._refused_compaction = signature
            return messages  # summarization failed → leave it to _snip_history

        compacted = spec.compaction_strategy.build_history(
            messages,
            summary,
            context_window_tokens=spec.context_window_tokens,
        )
        # dsh's convergence rule, applied to the AUTO path too: a summary that
        # does not shrink its source by volume is growth wearing a summary's
        # clothes — keep the original and let _snip_history bound the prompt.
        if self._history_chars(compacted) >= self._history_chars(messages):
            self._refused_compaction = signature
            logger.info(
                "Compaction summary for {} did not shrink the history; keeping it "
                "(not retried until the history changes)",
                spec.session_key or "default",
            )
            return messages
        # Bounded checkpoint re-injection: the PreCompact hook's
        # ``additional_contexts`` survive a successful compaction as a single
        # provider-agnostic user message. ``_build_precompact_checkpoint``
        # caps each context and the total block, so a runaway hook can never
        # blow the post-compaction window back open. When the hook blocks or
        # summarization fails we returned above — the checkpoint only ever
        # appears after a successful compaction.
        # A checkpoint must not turn successful compaction back into growth.
        # Bound it to the actual character reduction in addition to the
        # absolute hook-context cap.
        checkpoint_room = (
            self._history_chars(messages) - self._history_chars(compacted) - 1
        )
        checkpoint = _build_precompact_checkpoint(
            pre_contexts,
            total_limit=checkpoint_room,
        )
        if checkpoint:
            self._append_injected_messages(
                compacted,
                [{"role": "user", "content": checkpoint}],
            )
        self._refused_compaction = None
        if spec.post_compact_hook is not None:
            await self._call_tool_hook(spec.post_compact_hook, "auto")
        logger.info(
            "Compacted context for {}: {} → {} messages (est {} > {}·{:.0%} budget)",
            spec.session_key or "default",
            len(messages),
            len(compacted),
            estimate,
            budget,
            _COMPACT_TRIGGER_FRACTION,
        )
        self._notify_compaction_summary(spec, summary, messages, compacted, "auto")
        return compacted

    def _estimate_prompt(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> int | None:
        """Estimate the routed-request token size of ``messages``, or ``None``."""
        return spec.token_meter.measure(
            self.provider,
            spec.model,
            self._with_transient_context(
                messages,
                spec.transient_context_messages,
            ),
            spec.tool_definitions(),
        )

    async def _summarize(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        response_observer: Callable[[LLMResponse], Awaitable[None]] | None = None,
    ) -> str | None:
        """Ask the model for a handoff summary of ``messages``.

        The request replays the routed request's exact view — same governance
        composition, same transient context, same tool schemas (kept even
        though the summarizer never calls one: dropping them would shorten
        the token sequence and misalign every following token) — and appends
        only the compaction instruction. That makes this auxiliary call a
        genuine prefix of the last request the provider saw, so provider-side
        prefix/KV caching is reused instead of invalidated (the dsh rule).
        """
        request = self._request_view(spec, messages) + [
            {"role": "user", "content": _SUMMARIZATION_PROMPT}
        ]
        kwargs = self._build_request_kwargs(
            spec,
            request,
            tools=spec.tool_definitions(),
        )
        try:
            response = await self._await_provider_response(
                self.provider.chat_with_retry(**kwargs),
                timeout_s=resolve_request_timeout_s(spec.llm_timeout_s),
                streaming=False,
            )
        except Exception:
            logger.exception("compaction summarization call failed")
            return None
        if response_observer is not None:
            await response_observer(response)
        if getattr(response, "finish_reason", None) == "error":
            return None
        content = getattr(response, "content", None)
        return content.strip() if isinstance(content, str) and content.strip() else None

    @staticmethod
    def _history_chars(messages: list[dict[str, Any]]) -> int:
        return sum(len(str(m.get("content", ""))) for m in messages)

    async def compact_history(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, str]:
        """Summarize ``messages`` on demand — the manual `/compact` engine.

        Unlike :meth:`_maybe_compact`, this skips the automatic pressure
        gate (dsh's rule: manual compaction works even below pressure) and
        the compaction hooks — those are the AUTO path's policy points; a
        human asking directly is the policy. Returns the compacted history,
        or ``None`` with a stable reason the caller can surface verbatim:
        the model produced no usable summary, or the summary did not shrink
        anything (nothing worth replacing).
        """
        if sum(1 for m in messages if m.get("role") != "system") < 4:
            return None, "No compactable history yet."
        summary = await self._summarize(spec, messages)
        if not summary:
            return None, (
                "Compaction could not produce a useful summary. "
                "The conversation is unchanged."
            )
        compacted = spec.compaction_strategy.build_history(
            messages,
            summary,
            context_window_tokens=spec.context_window_tokens,
        )
        # Shrinkage is judged by VOLUME, not message count: replacing four
        # short turns with three longer ones is growth wearing a summary's
        # clothes (dsh's convergence rule — reject a summary that does not
        # shrink its source). Observed live: a short conversation "compacted"
        # 4 → 3 messages while gaining 1,347 characters.
        if self._history_chars(compacted) >= self._history_chars(messages):
            return None, (
                "Compaction would not shrink the conversation. "
                "The conversation is unchanged."
            )
        self._notify_compaction_summary(spec, summary, messages, compacted, "manual")
        return compacted, "compacted"

    def _notify_compaction_summary(
        self,
        spec: AgentRunSpec,
        summary: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        phase: str,
    ) -> None:
        """Deposit the handoff summary + anchors into the memory sink (P1-5).

        Pure fire-and-forget: a failing or absent sink never affects the
        compaction result. Anchors keep the summary retrievable and
        attributable (lesson 15: compressed summaries must carry session id,
        phase, and timestamps rather than vanishing into the vault).
        """
        if spec.compaction_summary_sink is None:
            return
        import time as _time

        anchor = {
            "session_key": spec.session_key or "default",
            "phase": phase,
            "at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "messages_before": len(before),
            "messages_after": len(after),
            "chars_before": self._history_chars(before),
            "chars_after": self._history_chars(after),
        }
        try:
            spec.compaction_summary_sink(summary, anchor)
        except Exception:  # noqa: BLE001 - memory work must never break the turn
            logger.debug("compaction summary sink failed", exc_info=True)

    def _overflow_reduce(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """One maximal head reduction that keeps a legal recent tail."""
        reduced = self._snip_history(spec, messages)
        if reduced != messages:
            return reduced
        system = [dict(item) for item in messages if item.get("role") == "system"]
        non_system = [dict(item) for item in messages if item.get("role") != "system"]
        if len(non_system) <= 1:
            return messages
        start = find_legal_message_start(non_system[1:])
        return system + non_system[1:][start:]

    @staticmethod
    def _build_compacted_history(
        messages: list[dict[str, Any]], summary: str
    ) -> list[dict[str, Any]]:
        """Replacement history via the default strategy (tests call this)."""
        return DEFAULT_COMPACTION_STRATEGY.build_history(messages, summary)

    def _snip_history(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not messages or not spec.context_window_tokens:
            return messages

        budget = self._context_budget(spec)
        if budget is None:
            return messages

        estimate = spec.token_meter.measure(
            self.provider,
            spec.model,
            messages,
            spec.tool_definitions(),
        )
        if estimate is None or estimate <= budget:
            return messages

        system_messages = [dict(msg) for msg in messages if msg.get("role") == "system"]
        non_system = [dict(msg) for msg in messages if msg.get("role") != "system"]
        if not non_system:
            return messages

        system_tokens = sum(estimate_message_tokens(msg) for msg in system_messages)
        remaining_budget = max(128, budget - system_tokens)
        kept: list[dict[str, Any]] = []
        kept_tokens = 0
        for message in reversed(non_system):
            msg_tokens = estimate_message_tokens(message)
            if kept and kept_tokens + msg_tokens > remaining_budget:
                break
            kept.append(message)
            kept_tokens += msg_tokens
        kept.reverse()

        if kept:
            for i, message in enumerate(kept):
                if message.get("role") == "user":
                    kept = kept[i:]
                    break
            else:
                for idx in range(len(non_system) - 1, -1, -1):
                    if non_system[idx].get("role") == "user":
                        kept = non_system[idx:]
                        break
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
        if not kept:
            kept = non_system[-min(len(non_system), 4) :]
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
        return system_messages + kept

    @classmethod
    def _with_transient_context(
        cls,
        messages: list[dict[str, Any]],
        context_messages: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        """Compose provider-neutral context around the latest user input.

        ``developer`` is an internal priority used by capability catalogs. Some
        supported providers do not accept that wire role, and both Anthropic
        Messages and OpenAI Responses expose one top-level instruction block.
        Merge existing system messages and transient developer guidance into a
        single system message, then place user-priority Turn context immediately
        before the canonical user request.
        """

        if not context_messages:
            return list(messages)

        privileged_contents: list[Any] = []
        non_system_messages: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                privileged_contents.append(message.get("content"))
            else:
                non_system_messages.append(dict(message))

        turn_context: list[dict[str, Any]] = []
        for message in context_messages:
            role = message.get("role")
            if role in {"developer", "system"}:
                privileged_contents.append(message.get("content"))
            else:
                turn_context.append(dict(message))

        composed: list[dict[str, Any]] = non_system_messages
        if privileged_contents:
            privileged_content: Any = privileged_contents[0]
            for content in privileged_contents[1:]:
                privileged_content = cls._merge_message_content(
                    privileged_content,
                    content,
                )
            composed = [
                {"role": "system", "content": privileged_content},
                *composed,
            ]

        if not turn_context:
            return composed
        insertion = next(
            (
                index
                for index in range(len(composed) - 1, -1, -1)
                if composed[index].get("role") == "user"
            ),
            len(composed),
        )
        return [
            *composed[:insertion],
            *turn_context,
            *composed[insertion:],
        ]

    def _partition_tool_batches(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
    ) -> list[list[ToolCallRequest]]:
        if not spec.concurrent_tools:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            get_tool = getattr(spec.tools, "get", None)
            tool = get_tool(tool_call.name) if callable(get_tool) else None
            can_batch = bool(tool and tool.concurrency_safe)
            if can_batch:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches
