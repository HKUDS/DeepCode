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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from core.agent_runtime.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    maybe_persist_tool_result,
    truncate_text,
)
from core.agent_runtime.hook import AgentHook, AgentHookContext
from core.agent_runtime.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_finalization_retry_message,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
    repeated_external_lookup_error,
)
from core.agent_runtime.tools.registry import ToolRegistry
from core.providers.base import LLMProvider, LLMResponse, ToolCallRequest

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_DEFAULT_MAX_ITERATIONS_MESSAGE = (
    "I reached the maximum number of tool call iterations ({max_iterations}) "
    "without completing the task. You can try breaking the task into smaller steps."
)
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_MAX_INJECTIONS_PER_TURN = 3
_MAX_INJECTION_CYCLES = 5
_SNIP_SAFETY_BUFFER = 1024
_MICROCOMPACT_KEEP_RECENT = 10
_MICROCOMPACT_MIN_CHARS = 500

# Summarization-based compaction (C4a). When the prompt nears the context
# budget, a model call condenses the conversation into a handoff summary that
# replaces old turns — semantic compaction, unlike the drop-based _snip_history
# fallback. The compacted history is returned and persisted by the session, so
# it survives across turns and is not re-summarized every step.
_COMPACT_TRIGGER_FRACTION = 0.9  # summarize once the prompt exceeds 90% of budget
_COMPACT_KEEP_USER_CHARS = (
    60_000  # recent user messages kept verbatim before the summary
)
_SUMMARIZATION_PROMPT = (
    "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff "
    "summary for another agent that will resume this task.\n\n"
    "Include:\n"
    "- Current progress and key decisions made\n"
    "- Important context, constraints, or user preferences\n"
    "- What remains to be done (clear next steps)\n"
    "- Any critical data, examples, file paths, or references needed to continue\n\n"
    "Be concise, structured, and focused on helping the next agent seamlessly "
    "continue the work."
)
_SUMMARY_PREFIX = (
    "An earlier agent worked on this task and produced the summary below of its "
    "progress and the state of the tools it used. Build on this work and avoid "
    "duplicating it. Here is the summary:"
)
_COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "exec",
        "grep",
        "glob",
        "web_search",
        "web_fetch",
        "list_dir",
    }
)
_BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
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
    max_injection_cycles: int | None = None
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
    # Stop hook (C3.1): fires when the turn would end cleanly. If it asks to
    # continue (``.block`` with ``.block_reason``), the reason is injected as a
    # follow-up prompt and the loop keeps going. ``stop_hook_active`` is passed
    # so a well-behaved hook stops blocking after its first continuation.
    stop_hook: Any | None = None


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
        injection_cycles: int,
        *,
        phase: str = "after error",
        iteration: int | None = None,
    ) -> tuple[bool, int]:
        max_cycles = (
            spec.max_injection_cycles
            if spec.max_injection_cycles is not None
            else _MAX_INJECTION_CYCLES
        )
        if injection_cycles >= max_cycles:
            return False, injection_cycles
        injections = await self._drain_injections(spec)
        if not injections:
            return False, injection_cycles
        injection_cycles += 1
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
            "Injected {} follow-up message(s) {} ({}/{})",
            len(injections),
            phase,
            injection_cycles,
            max_cycles,
        )
        return True, injection_cycles

    async def _drain_injections(self, spec: AgentRunSpec) -> list[dict[str, Any]]:
        if spec.injection_callback is None:
            return []
        try:
            signature = inspect.signature(spec.injection_callback)
            accepts_limit = "limit" in signature.parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_limit:
                items = await spec.injection_callback(limit=_MAX_INJECTIONS_PER_TURN)
            else:
                items = await spec.injection_callback()
        except Exception:
            logger.exception("injection_callback failed")
            return []
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            if (
                isinstance(item, dict)
                and item.get("role") == "user"
                and "content" in item
            ):
                injected_messages.append(item)
                continue
            text = getattr(item, "content", str(item))
            if text.strip():
                injected_messages.append({"role": "user", "content": text})
        if len(injected_messages) > _MAX_INJECTIONS_PER_TURN:
            dropped = len(injected_messages) - _MAX_INJECTIONS_PER_TURN
            logger.warning(
                "Injection callback returned {} messages, capping to {} ({} dropped)",
                len(injected_messages),
                _MAX_INJECTIONS_PER_TURN,
                dropped,
            )
            injected_messages = injected_messages[:_MAX_INJECTIONS_PER_TURN]
        return injected_messages

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
        empty_content_retries = 0
        length_recovery_count = 0
        had_injections = False
        injection_cycles = 0
        stop_hook_active = False  # C3.1: set once a Stop hook has forced a continuation

        for iteration in range(spec.max_iterations):
            if spec.should_stop_callback is not None:
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
                    break

            # Summarization-based compaction (C4a): when the running history
            # nears the budget, replace old turns with a model summary. Persisted
            # in `messages` so later iterations (and turns) reuse it.
            messages = await self._maybe_compact(spec, messages)

            try:
                messages_for_model = self._drop_orphan_tool_results(messages)
                messages_for_model = self._backfill_missing_tool_results(
                    messages_for_model
                )
                messages_for_model = self._microcompact(messages_for_model)
                messages_for_model = self._apply_tool_result_budget(
                    spec, messages_for_model
                )
                messages_for_model = self._snip_history(spec, messages_for_model)
                messages_for_model = self._drop_orphan_tool_results(messages_for_model)
                messages_for_model = self._backfill_missing_tool_results(
                    messages_for_model
                )
            except Exception as exc:
                logger.warning(
                    "Context governance failed on turn {} for {}: {}; applying minimal repair",
                    iteration,
                    spec.session_key or "default",
                    exc,
                )
                try:
                    messages_for_model = self._drop_orphan_tool_results(messages)
                    messages_for_model = self._backfill_missing_tool_results(
                        messages_for_model
                    )
                except Exception:
                    messages_for_model = messages
            context = AgentHookContext(iteration=iteration, messages=messages)
            await hook.before_iteration(context)
            response = await self._request_model(
                spec, messages_for_model, hook, context
            )
            raw_usage = self._usage_dict(response.usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            self._accumulate_usage(usage, raw_usage)

            if response.should_execute_tools:
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                tools_used.extend(tc.name for tc in response.tool_calls)
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
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
                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    (
                        should_continue,
                        injection_cycles,
                    ) = await self._try_drain_injections(
                        spec,
                        messages,
                        None,
                        injection_cycles,
                        phase="after tool error",
                    )
                    if should_continue:
                        had_injections = True
                        continue
                    break
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "tools_completed",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": completed_tool_results,
                        "pending_tool_calls": [],
                    },
                )
                empty_content_retries = 0
                length_recovery_count = 0
                _drained, injection_cycles = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    injection_cycles,
                    phase="after tool execution",
                )
                if _drained:
                    had_injections = True
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
                        iteration,
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
                    iteration,
                    spec.session_key or "default",
                    empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                response = await self._request_finalization_retry(
                    spec, messages_for_model
                )
                retry_usage = self._usage_dict(response.usage)
                self._accumulate_usage(usage, retry_usage)
                raw_usage = self._merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = dict(raw_usage)
                context.tool_calls = list(response.tool_calls)
                clean = hook.finalize_content(context, response.content)

            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration,
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
                            thinking_blocks=response.thinking_blocks,
                        )
                    )
                    messages.append(build_length_recovery_message())
                    await hook.after_iteration(context)
                    continue

            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

            should_continue, injection_cycles = await self._try_drain_injections(
                spec,
                messages,
                assistant_message,
                injection_cycles,
                phase="after final response",
                iteration=iteration,
            )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            if response.finish_reason == "error":
                final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    injection_cycles,
                    phase="after LLM error",
                )
                if should_continue:
                    had_injections = True
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
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec,
                    messages,
                    None,
                    injection_cycles,
                    phase="after empty response",
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            messages.append(
                assistant_message
                or build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
            )
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": iteration,
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
            # `stop_hook_active` flag lets a well-behaved hook stand down after
            # one continuation; max_iterations remains the hard backstop.
            continuation = await self._run_stop_hook(spec, stop_hook_active)
            if continuation is not None:
                stop_hook_active = True
                had_injections = True
                self._append_injected_messages(
                    messages, [{"role": "user", "content": continuation}]
                )
                await hook.after_iteration(context)
                continue
            break
        else:
            stop_reason = "max_iterations"
            template = spec.max_iterations_message or _DEFAULT_MAX_ITERATIONS_MESSAGE
            final_content = template.format(max_iterations=spec.max_iterations)
            self._append_final_message(messages, final_content)
            (
                drained_after_max_iterations,
                injection_cycles,
            ) = await self._try_drain_injections(
                spec,
                messages,
                None,
                injection_cycles,
                phase="after max_iterations",
            )
            if drained_after_max_iterations:
                had_injections = True

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

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
    ):
        timeout_s: float | None = spec.llm_timeout_s
        if timeout_s is None:
            raw = (
                os.environ.get("DEEPCODE_LLM_TIMEOUT_S")
                or os.environ.get("NANOBOT_LLM_TIMEOUT_S")
                or "300"
            ).strip()
            try:
                timeout_s = float(raw)
            except (TypeError, ValueError):
                timeout_s = 300.0
        if timeout_s is not None and timeout_s <= 0:
            timeout_s = None

        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=spec.tools.get_definitions(),
        )
        if hook.wants_streaming():

            async def _stream(delta: str) -> None:
                await hook.on_stream(context, delta)

            coro = self.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
            )
        else:
            coro = self.provider.chat_with_retry(**kwargs)

        if timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            return LLMResponse(
                content=f"Error calling LLM: timed out after {timeout_s:g}s",
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
        kwargs = self._build_request_kwargs(spec, retry_messages, tools=None)
        return await self.provider.chat_with_retry(**kwargs)

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
        try:
            if tool is not None:
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
        return f"{result}\n\n{joined}"

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

    @staticmethod
    def _microcompact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compactable_indices: list[int] = []
        for idx, msg in enumerate(messages):
            if msg.get("role") == "tool" and msg.get("name") in _COMPACTABLE_TOOLS:
                compactable_indices.append(idx)

        if len(compactable_indices) <= _MICROCOMPACT_KEEP_RECENT:
            return messages

        stale = compactable_indices[
            : len(compactable_indices) - _MICROCOMPACT_KEEP_RECENT
        ]
        updated: list[dict[str, Any]] | None = None
        for idx in stale:
            msg = messages[idx]
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < _MICROCOMPACT_MIN_CHARS:
                continue
            name = msg.get("name", "tool")
            summary = f"[{name} result omitted from context]"
            if updated is None:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = summary

        return updated if updated is not None else messages

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
        self, spec: AgentRunSpec, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Summarize the conversation into a handoff summary when it nears the
        context budget (C4a) — semantic compaction that replaces old turns,
        unlike the drop-based ``_snip_history`` fallback that still follows.

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
        try:
            estimate, _ = estimate_prompt_tokens_chain(
                self.provider, spec.model, messages, spec.tools.get_definitions()
            )
        except Exception:
            return messages
        if estimate <= int(budget * _COMPACT_TRIGGER_FRACTION):
            return messages

        if spec.pre_compact_hook is not None:
            pre = await self._call_tool_hook(spec.pre_compact_hook, "auto")
            if pre is not None and getattr(pre, "block", False):
                return messages  # a PreCompact hook aborted compaction this turn

        summary = await self._summarize(spec, messages)
        if not summary:
            return messages  # summarization failed → leave it to _snip_history

        compacted = self._build_compacted_history(messages, summary)
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
        return compacted

    async def _summarize(
        self, spec: AgentRunSpec, messages: list[dict[str, Any]]
    ) -> str | None:
        """Ask the model for a handoff summary of ``messages`` (no tools)."""
        request = list(messages) + [{"role": "user", "content": _SUMMARIZATION_PROMPT}]
        # Fit the summarization request itself within budget, and keep it valid.
        request = self._snip_history(spec, request)
        request = self._drop_orphan_tool_results(request)
        request = self._backfill_missing_tool_results(request)
        kwargs = self._build_request_kwargs(spec, request, tools=None)
        try:
            response = await self.provider.chat_with_retry(**kwargs)
        except Exception:
            logger.exception("compaction summarization call failed")
            return None
        if getattr(response, "finish_reason", None) == "error":
            return None
        content = getattr(response, "content", None)
        return content.strip() if isinstance(content, str) and content.strip() else None

    @staticmethod
    def _build_compacted_history(
        messages: list[dict[str, Any]], summary: str
    ) -> list[dict[str, Any]]:
        """Replacement history: system messages + recent user messages (verbatim,
        within a char budget) + the summary as a final user message. Assistant
        and tool turns are dropped — the summary stands in for them."""
        system = [dict(m) for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        kept_users: list[dict[str, Any]] = []
        remaining = _COMPACT_KEEP_USER_CHARS
        for message in reversed(non_system):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if len(content) > remaining:
                if remaining > 0:
                    kept_users.append({"role": "user", "content": content[-remaining:]})
                break
            kept_users.append({"role": "user", "content": content})
            remaining -= len(content)
        kept_users.reverse()
        summary_message = {"role": "user", "content": f"{_SUMMARY_PREFIX}\n{summary}"}
        return system + kept_users + [summary_message]

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

        estimate, _ = estimate_prompt_tokens_chain(
            self.provider,
            spec.model,
            messages,
            spec.tools.get_definitions(),
        )
        if estimate <= budget:
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
