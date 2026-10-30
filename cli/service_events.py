"""Render service events through the existing TUI/exec event vocabulary.

Durable events arrive in sequence order. Streamed text arrives as live
``item.delta`` notifications, whose offsets count the UTF-8 bytes of the
item's text on one channel; the renderer remembers how much of each streaming
item it has shown.
"""

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
        self._shown = {}  # (item id, channel) -> UTF-8 bytes rendered
        self._started = set()
        self._skills = set()
        self._turns_with_error_items = set()

    def seed(self, items, *, turn_id=None):
        """Adopt a Turn that was already running before the replay cursor.

        Only items still in progress can receive further deltas, so completed
        ones are not remembered. ``turn_id`` marks the Turn as one this
        renderer follows, so its terminal event is rendered.
        """

        if turn_id is not None:
            self._started.add(turn_id)
        for item in items:
            if item.status.value not in {"in_progress", "pending"}:
                continue
            self._remember(
                item.id,
                item.kind.value,
                item.payload.get("messageId") or item.id,
                item.turn_id,
            )
            # Followed from here on: what streamed before is not shown again.
            for channel, text in _channel_texts(item.kind.value, item.payload):
                self._shown[item.id, channel] = len(text.encode())

    def join(self, turn_id, items):
        """Follow a Turn that was running before this client attached.

        Unlike :meth:`seed`, nothing of it is on screen yet: the Turn starts,
        and each item still in progress is shown as it stands (the text
        streamed so far, a running tool), so live deltas continue it rather
        than its completion printing the whole text again.
        """

        self._started.add(turn_id)
        messages = [TurnStarted()]
        for item in items:
            if item.status.value in {"in_progress", "pending"}:
                messages += self._item_messages(
                    turn_id,
                    {
                        "id": item.id,
                        "kind": item.kind.value,
                        "status": item.status.value,
                        "summary": item.summary,
                        "payload": item.payload,
                    },
                    created=True,
                )
        return _events(f"join:{turn_id}", messages)

    def _remember(self, item_id, kind, message_id, turn_id):
        if len(self._items) >= 2048 and item_id not in self._items:
            raise RuntimeError("Too many active timeline items to render")
        self._items[item_id] = (kind, message_id, turn_id)

    def _forget(self, item_ids):
        for item_id in item_ids:
            self._items.pop(item_id, None)
        self._shown = {
            key: shown for key, shown in self._shown.items() if key[0] in self._items
        }

    def convert_live(self, delta):
        """Render a live delta; None when an earlier one was missed."""

        state = self._items.get(delta["itemId"])
        if state is None:
            # The item began before this client followed it; its completion
            # carries the whole text.
            return []
        key = (delta["itemId"], delta["channel"])
        shown = self._shown.get(key, 0)
        if delta["offset"] > shown:
            return None
        if delta["nextOffset"] <= shown:
            return []
        unseen = delta["delta"].encode()[shown - delta["offset"] :].decode()
        self._shown[key] = delta["nextOffset"]
        return _events(
            f"live:{delta['itemId']}:{shown}",
            self._streamed(state, delta["itemId"], delta["channel"], unseen),
        )

    def convert_streamed(self, items):
        """Render streamed text of ``items`` (read now) not yet shown."""

        messages = []
        for item in items:
            state = self._items.get(item.id)
            if state is None or item.status.value != "in_progress":
                continue
            for channel, text in _channel_texts(item.kind.value, item.payload):
                data = text.encode()
                shown = self._shown.get((item.id, channel), 0)
                if len(data) > shown:
                    self._shown[item.id, channel] = len(data)
                    messages += self._streamed(
                        state, item.id, channel, data[shown:].decode()
                    )
        return _events("caught-up", messages)

    @staticmethod
    def _streamed(state, item_id, channel, text):
        kind, message_id, _ = state
        if kind == "assistant_message":
            return [AgentMessageDelta(text, message_id)]
        if kind == "reasoning_summary":
            return [AgentReasoningDelta(item_id, ReasoningChannel(channel), text)]
        return []

    def convert(self, event: DomainEvent):
        return _events(event.id, self._messages(event))

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
            followed = event.turn_id in self._started
            reported_error = event.turn_id in self._turns_with_error_items
            self._started.discard(event.turn_id)
            self._turns_with_error_items.discard(event.turn_id)
            self._skills = {key for key in self._skills if key[0] != event.turn_id}
            self._forget(
                [
                    item_id
                    for item_id, value in self._items.items()
                    if value[2] == event.turn_id
                ]
            )
            if not followed:
                # A Turn this client never saw start (a queued Turn cancelled
                # elsewhere, or one that ended before the replay cursor) must
                # not settle the Turn that is currently streaming.
                return []
            messages = []
            error_message = (turn.get("errorMessage") or "").strip()
            if turn.get("status") == "failed" and error_message and not reported_error:
                # Some failures (protocol or projection errors) record no error
                # item; without this the user sees only a bare stop reason.
                messages.append(ErrorEvent(error_message))
            messages.append(
                TaskComplete(
                    None, turn.get("stopReason") or turn.get("status") or "interrupted"
                )
            )
            return messages
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
            # Only in the history of older services, which logged streamed text.
            state = self._items.get(event.item_id)
            if state is None:
                # The item began before this client's cursor (it attached
                # mid-stream or raced a Turn ending). Its completion carries
                # the full text, so skipping the fragment loses nothing.
                return []
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
        return self._item_messages(
            event.turn_id, payload["item"], created=event.type == "item.created"
        )

    def _item_messages(self, turn_id, item, *, created):
        """What one item's creation or update shows; ``item`` is its view."""

        kind, data = item["kind"], item["payload"]
        identity = data.get("messageId") or item["id"]
        running = item["status"] in {"in_progress", "pending"}
        if running:
            self._remember(item["id"], kind, identity, turn_id)
            if created:
                # Its text so far is rendered below; live deltas continue it.
                for channel, text in _channel_texts(kind, data):
                    self._shown[item["id"], channel] = len(text.encode())
        else:
            self._forget([item["id"]])
        if kind == "user_message":
            loaded = []
            for value in data.get("skills", []):
                if not isinstance(value, dict):
                    continue
                key = (turn_id, value["skillId"])
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
                preview = data.get("resultPreview", "")
                if data.get("interrupted") and not preview:
                    preview = "Interrupted"
                return [
                    ToolCompleted(
                        data["callId"],
                        data["name"],
                        data.get("isError", item["status"] != "completed"),
                        preview,
                        # Only a refusal declines a tool item.
                        denied=item["status"] == "declined",
                    )
                ]
        if kind == "error":
            message = data.get("message") or item["summary"]
            if data.get("severity") == "warning":
                # Not the Turn's failure: its reason must still be shown.
                return [ErrorEvent(message, warning=True)]
            if turn_id is not None:
                self._turns_with_error_items.add(turn_id)
            return [ErrorEvent(message)]
        return []


def _events(identity, messages):
    return [
        Event(f"{identity}:{index}", message) for index, message in enumerate(messages)
    ]


def _channel_texts(kind, payload):
    """An item's streamed text per live channel."""

    if kind == "assistant_message":
        text = payload.get("text")
        return [("text", text if isinstance(text, str) else "")]
    if kind == "reasoning_summary":
        reasoning = ReasoningPayload.from_dict(payload)
        return [
            (ReasoningChannel.SUMMARY.value, reasoning.summary_text),
            (ReasoningChannel.PROVIDER_TRACE.value, reasoning.trace_text),
        ]
    return []
