"""Render durable service events through the existing TUI/exec event vocabulary."""

from __future__ import annotations

from cli.rpc_models import from_view
from core.domain.event import DomainEvent
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    ErrorEvent,
    Event,
    ModelUsageRecorded,
    PlanStep,
    PlanUpdated,
    SkillLoaded,
    TaskComplete,
    ToolActivity,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
)
from core.reasoning import ReasoningChannel, ReasoningPayload
from core.skills.models import SkillInvocation, SkillInvocationKind


class ServiceEventRenderer:
    """Keep only active item identities; the caller owns the contiguous cursor."""

    def __init__(self):
        self._items = {}
        self._started = set()
        self._skills = set()

    def seed(self, items):
        for item in items:
            self._remember(
                item.id,
                item.kind.value,
                item.payload.get("messageId") or item.id,
                item.turn_id,
            )

    def _remember(self, item_id, kind, message_id, turn_id):
        if len(self._items) >= 2048 and item_id not in self._items:
            raise RuntimeError("Too many active timeline items to render")
        self._items[item_id] = (kind, message_id, turn_id)

    def convert(self, event: DomainEvent):
        messages = self._messages(event)
        return [
            Event(f"{event.id}:{index}", message)
            for index, message in enumerate(messages)
        ]

    def _messages(self, event):
        payload = event.payload
        turn = payload.get("turn", {})
        if (
            event.type in {"turn.started", "turn.updated"}
            and turn.get("status") in {"running", "queued"}
            and event.turn_id not in self._started
        ):
            self._started.add(event.turn_id)
            return [TurnStarted()]
        if event.type in {
            "turn.completed",
            "turn.failed",
            "turn.interrupted",
            "turn.recovered",
        }:
            self._started.discard(event.turn_id)
            self._skills = {key for key in self._skills if key[0] != event.turn_id}
            self._items = {
                key: value
                for key, value in self._items.items()
                if value[2] != event.turn_id
            }
            return [
                TaskComplete(
                    None, turn.get("stopReason") or turn.get("status") or "interrupted"
                )
            ]
        if event.type == "turn.usage.recorded":
            return [
                ModelUsageRecorded(
                    response_ordinal=payload["responseOrdinal"], usage=payload["usage"]
                )
            ]
        if event.type == "turn.plan.updated":
            plan = payload.get("plan", {})
            return [
                PlanUpdated(
                    tuple(from_view(PlanStep, step) for step in plan.get("steps", [])),
                    plan.get("explanation"),
                )
            ]
        if event.type == "item.delta":
            state = self._items.get(event.item_id)
            if state is None:
                raise RuntimeError("A streamed item is missing its initial state")
            kind, message_id, _ = state
            if kind == "assistant_message":
                return [AgentMessageDelta(payload["delta"], message_id)]
            if kind == "reasoning_summary":
                return [
                    AgentReasoningDelta(
                        event.item_id,
                        ReasoningChannel(payload["reasoningChannel"]),
                        payload["delta"],
                    )
                ]
            return []
        if event.type not in {"item.created", "item.updated"}:
            return []
        item = payload["item"]
        kind, data = item["kind"], item["payload"]
        identity = data.get("messageId") or item["id"]
        running = item["status"] in {"in_progress", "pending"}
        created = event.type == "item.created"
        if running:
            self._remember(item["id"], kind, identity, event.turn_id)
        else:
            self._items.pop(item["id"], None)
        if kind == "user_message":
            loaded = []
            for value in data.get("skills", []):
                if not isinstance(value, dict):
                    continue
                key = (event.turn_id, value["skillId"])
                if key not in self._skills:
                    self._skills.add(key)
                    loaded.append(
                        SkillLoaded(
                            SkillInvocation(
                                value["skillId"],
                                value["name"],
                                value["revision"],
                                value["source"],
                                SkillInvocationKind(value["invocation"]),
                            )
                        )
                    )
            return loaded
        if kind == "assistant_message":
            if running:
                return (
                    [AgentMessageDelta(data["text"], identity)]
                    if created and data.get("text")
                    else []
                )
            phase = AgentMessagePhase(data.get("phase", "final_answer"))
            if phase is AgentMessagePhase.COMMENTARY:
                return [
                    AgentMessageCompleted(
                        identity, data.get("text", item["summary"]), phase
                    )
                ]
            return [AgentMessage(data.get("text", item["summary"]), identity, phase)]
        if kind == "reasoning_summary":
            reasoning = ReasoningPayload.from_dict(data)
            if running:
                if not created:
                    return []
                messages = [AgentReasoningStarted(item["id"], reasoning.effort)]
                if reasoning.summary_text:
                    messages.append(
                        AgentReasoningDelta(
                            item["id"], ReasoningChannel.SUMMARY, reasoning.summary_text
                        )
                    )
                if reasoning.trace_text:
                    messages.append(
                        AgentReasoningDelta(
                            item["id"],
                            ReasoningChannel.PROVIDER_TRACE,
                            reasoning.trace_text,
                        )
                    )
                return messages
            return [
                AgentReasoningCompleted(
                    item["id"],
                    reasoning.summary_text,
                    reasoning.trace_text,
                    reasoning.availability,
                    reasoning.effort,
                    reasoning.duration_ms,
                )
            ]
        if (
            kind in {"tool_call", "command_execution", "file_change", "test_result"}
            and "callId" in data
        ):
            if running and created:
                return [
                    ToolStarted(
                        data["callId"],
                        data["name"],
                        data.get("detail", ""),
                        from_view(ToolActivity, data["activity"])
                        if data.get("activity")
                        else None,
                    )
                ]
            if not running:
                return [
                    ToolCompleted(
                        data["callId"],
                        data["name"],
                        data.get("isError", item["status"] != "completed"),
                        data.get("resultPreview", ""),
                    )
                ]
        if kind == "error":
            return [ErrorEvent(data.get("message") or item["summary"])]
        return []
