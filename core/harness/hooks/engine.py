"""HooksEngine — dispatch events to configured hook handlers (C3).

Ports the reference agent's ``registry.rs`` + ``engine/mod.rs`` +
``dispatcher.rs`` + per-event fold. For each event the engine:

1. **selects** handlers by event name and matcher (a tool name for tool
   events, the source string for ``SessionStart``);
2. **executes** the selected commands concurrently, feeding each the JSON
   payload on stdin;
3. **folds** their decisions in declaration order — block if *any* blocks
   (first block's reason wins), accumulate every ``additionalContext``, and
   take the last-completed ``updatedInput`` rewrite.

The engine is created once per session (it holds ``cwd`` + ``session_id``); an
empty handler list makes every ``run_*`` a cheap no-op.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from core.harness.hooks.discovery import Handler, discover_hooks
from core.harness.hooks.events import matches_matcher
from core.harness.hooks.execution import (
    HandlerDecision,
    parse_handler_output,
    run_command,
)


@dataclass(slots=True)
class _FoldedOutcome:
    block: bool = False
    block_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    updated_input: dict[str, Any] | None = None
    system_messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# Public per-event outcomes (only the fields a caller acts on).


@dataclass(slots=True)
class PreToolUseOutcome:
    block: bool = False
    block_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)
    updated_input: dict[str, Any] | None = None


@dataclass(slots=True)
class PostToolUseOutcome:
    block: bool = False
    block_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContextOutcome:
    """SessionStart / UserPromptSubmit: inject context, maybe block (prompt)."""

    block: bool = False
    block_reason: str | None = None
    additional_contexts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StopOutcome:
    block: bool = False  # block == "do not stop; keep going"
    block_reason: str | None = None


@dataclass(slots=True)
class PermissionRequestOutcome:
    """A hook verdict in the approval path: allow / deny / no verdict (None)."""

    decision: str | None = None  # "allow" | "deny" | None
    message: str = ""


class HooksEngine:
    """Per-session dispatcher over discovered hook handlers."""

    def __init__(
        self,
        handlers: list[Handler],
        cwd: str,
        session_id: str,
        *,
        model: str = "",
        permission_mode: str = "default",
    ) -> None:
        self._handlers = list(handlers)
        self._cwd = cwd
        self._session_id = session_id
        self._model = model
        self._permission_mode = permission_mode

    @classmethod
    def discover(
        cls,
        workspace: str,
        session_id: str,
        home: str | None = None,
        *,
        model: str = "",
        permission_mode: str = "default",
    ):
        """Build an engine for ``workspace``, or ``None`` if no hooks configured."""
        result = discover_hooks(workspace, home=home)
        if not result.handlers:
            return None, result.warnings
        engine = cls(
            result.handlers,
            workspace,
            session_id,
            model=model,
            permission_mode=permission_mode,
        )
        return engine, result.warnings

    def has_event(self, event_name: str) -> bool:
        return any(h.event_name == event_name for h in self._handlers)

    # -- per-event entry points ------------------------------------------------

    async def run_pre_tool_use(
        self, tool_name: str, tool_input: Any, *, tool_use_id: str = ""
    ) -> PreToolUseOutcome:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
        }
        folded = await self._dispatch("PreToolUse", tool_name, payload)
        return PreToolUseOutcome(
            block=folded.block,
            block_reason=folded.block_reason,
            additional_contexts=folded.additional_contexts,
            updated_input=None if folded.block else folded.updated_input,
        )

    async def run_post_tool_use(
        self,
        tool_name: str,
        tool_input: Any,
        tool_response: Any,
        *,
        tool_use_id: str = "",
    ) -> PostToolUseOutcome:
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": tool_response,
            "tool_use_id": tool_use_id,
        }
        folded = await self._dispatch("PostToolUse", tool_name, payload)
        return PostToolUseOutcome(
            block=folded.block,
            block_reason=folded.block_reason,
            additional_contexts=folded.additional_contexts,
        )

    async def run_session_start(self, source: str = "startup") -> ContextOutcome:
        payload = {"hook_event_name": "SessionStart", "source": source}
        folded = await self._dispatch("SessionStart", source, payload)
        return ContextOutcome(
            block=folded.block,
            block_reason=folded.block_reason,
            additional_contexts=folded.additional_contexts,
        )

    async def run_user_prompt_submit(self, prompt: str) -> ContextOutcome:
        payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt}
        folded = await self._dispatch("UserPromptSubmit", None, payload)
        return ContextOutcome(
            block=folded.block,
            block_reason=folded.block_reason,
            additional_contexts=folded.additional_contexts,
        )

    async def run_stop(self, stop_hook_active: bool = False) -> StopOutcome:
        payload = {"hook_event_name": "Stop", "stop_hook_active": stop_hook_active}
        folded = await self._dispatch("Stop", None, payload)
        return StopOutcome(block=folded.block, block_reason=folded.block_reason)

    async def run_pre_compact(self, trigger: str = "auto") -> ContextOutcome:
        """Before a summarization pass. A ``block`` (continue:false) asks to skip
        compaction this turn; the matcher runs against ``trigger`` (auto/manual)."""
        payload = {"hook_event_name": "PreCompact", "trigger": trigger}
        folded = await self._dispatch("PreCompact", trigger, payload)
        return ContextOutcome(block=folded.block, block_reason=folded.block_reason)

    async def run_post_compact(self, trigger: str = "auto") -> ContextOutcome:
        """After a summarization pass — a notification hook (state saved, etc.)."""
        payload = {"hook_event_name": "PostCompact", "trigger": trigger}
        await self._dispatch("PostCompact", trigger, payload)
        return ContextOutcome()

    async def run_permission_request(
        self, tool_name: str, tool_input: Any, *, tool_use_id: str = ""
    ) -> PermissionRequestOutcome:
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
        }
        completions = await self._run_handlers("PermissionRequest", tool_name, payload)
        # Fold conservatively: any deny wins immediately (declaration order);
        # otherwise the last allow wins; otherwise no verdict.
        resolved_allow = False
        for _order, dec in sorted(completions, key=lambda item: item[0]):
            if dec.status == "failed":
                continue
            if dec.permission == "deny":
                return PermissionRequestOutcome(
                    decision="deny", message=dec.block_reason or "denied by hook"
                )
            if dec.permission == "allow":
                resolved_allow = True
        return PermissionRequestOutcome(decision="allow" if resolved_allow else None)

    # SubagentStart / SubagentStop mirror SessionStart / Stop but for a spawned
    # sub-agent's own session; the caller passes the sub-agent's id/type as
    # context so hooks can tell which sub-agent they are running inside.

    async def run_subagent_start(
        self, agent_id: str, agent_type: str = "subagent", source: str = "startup"
    ) -> ContextOutcome:
        payload = {
            "hook_event_name": "SubagentStart",
            "source": source,
            "agent_id": agent_id,
            "agent_type": agent_type,
        }
        folded = await self._dispatch("SubagentStart", source, payload)
        return ContextOutcome(
            block=folded.block,
            block_reason=folded.block_reason,
            additional_contexts=folded.additional_contexts,
        )

    async def run_subagent_stop(
        self,
        agent_id: str,
        agent_type: str = "subagent",
        stop_hook_active: bool = False,
    ) -> StopOutcome:
        payload = {
            "hook_event_name": "SubagentStop",
            "agent_id": agent_id,
            "agent_type": agent_type,
            "stop_hook_active": stop_hook_active,
        }
        folded = await self._dispatch("SubagentStop", None, payload)
        return StopOutcome(block=folded.block, block_reason=folded.block_reason)

    # -- dispatch core ---------------------------------------------------------

    def _select(self, event_name: str, matcher_input: str | None) -> list[Handler]:
        return [
            h
            for h in self._handlers
            if h.event_name == event_name and matches_matcher(h.matcher, matcher_input)
        ]

    async def _run_handlers(
        self, event_name: str, matcher_input: str | None, payload_fields: dict
    ) -> list[tuple[int, HandlerDecision]]:
        """Select, execute (concurrently), and return per-handler decisions.

        Returns ``(display_order, decision)`` pairs in completion order — the
        caller folds them. Empty when no handler matches.
        """
        selected = self._select(event_name, matcher_input)
        if not selected:
            return []

        payload = {
            "session_id": self._session_id,
            "cwd": self._cwd,
            "model": self._model,
            "permission_mode": self._permission_mode,
            **payload_fields,
        }
        payload_json = json.dumps(payload)
        completions: list[tuple[int, HandlerDecision]] = []

        async def _run_one(handler: Handler) -> None:
            result = await run_command(handler, payload_json, self._cwd)
            completions.append(
                (handler.display_order, parse_handler_output(event_name, result))
            )

        await asyncio.gather(*(_run_one(h) for h in selected))
        return completions

    async def _dispatch(
        self, event_name: str, matcher_input: str | None, payload_fields: dict
    ) -> _FoldedOutcome:
        return self._fold(
            await self._run_handlers(event_name, matcher_input, payload_fields)
        )

    @staticmethod
    def _fold(completions: list[tuple[int, HandlerDecision]]) -> _FoldedOutcome:
        folded = _FoldedOutcome()
        # Reason / context fold in declaration order (stable reporting); the
        # updatedInput rewrite follows completion order (last writer wins).
        by_declaration = sorted(completions, key=lambda item: item[0])
        for _order, decision in by_declaration:
            if decision.system_message:
                folded.system_messages.append(decision.system_message)
            if decision.invalid_reason:
                folded.warnings.append(decision.invalid_reason)
            if decision.error:
                folded.warnings.append(decision.error)
            # A failed handler (spawn error, timeout, invalid or unsupported
            # output) contributes no context, block, or rewrite — only a warning.
            if decision.status == "failed":
                continue
            if decision.additional_context:
                folded.additional_contexts.append(decision.additional_context)
            if decision.block and not folded.block:
                folded.block = True
                folded.block_reason = decision.block_reason
        for _order, decision in completions:  # completion order
            if (
                decision.status != "failed"
                and decision.updated_input is not None
                and not decision.block
            ):
                folded.updated_input = decision.updated_input
        return folded
