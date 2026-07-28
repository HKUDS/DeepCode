"""Projection of live SQ/EQ events into durable timeline items."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from core.application.event_service import EventBroker
from core.application.turn_usage import (
    TURN_USAGE_EVENT_TYPE,
    add_usage,
    normalize_usage,
)
from core.application.views import item_view, timestamp
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.events import (
    AgentMessage,
    AgentMessageCompleted,
    AgentMessageDelta,
    AgentMessagePhase,
    AgentReasoningCompleted,
    AgentReasoningDelta,
    AgentReasoningStarted,
    AgentReasoningSummary,
    ErrorEvent,
    Event,
    ModelUsageRecorded,
    PlanUpdated,
    SkillLoaded,
    TaskComplete,
    ToolActivityKind,
    ToolCompleted,
    ToolStarted,
    TurnStarted,
)
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository
from core.reasoning import ReasoningChannel, ReasoningPayload

_STREAM_FLUSH_INTERVAL_S = 0.05
_STREAM_FLUSH_MIN_CHARS = 256


class TurnEventProjector:
    """Map live events into the rebuildable Desktop execution projection."""

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        *,
        thread_id: str,
        turn_id: str,
    ) -> None:
        self.database = database
        self.broker = broker
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.final_text: str | None = None
        self.stop_reason: str | None = None
        self.saw_terminal = False
        self._assistant_item_ids: dict[str, str] = {}
        self._assistant_texts: dict[str, str] = {}
        self._last_delta_flush: dict[str, float] = {}
        self._last_flushed_length: dict[str, int] = {}
        self._saw_final_assistant = False
        self._reasoning_item_ids: dict[str, str] = {}
        self._reasoning_payloads: dict[str, ReasoningPayload] = {}
        self._reasoning_last_flush: dict[tuple[str, ReasoningChannel], float] = {}
        self._reasoning_last_length: dict[tuple[str, ReasoningChannel], int] = {}
        self._tool_item_ids: dict[str, str] = {}
        self._plan_tool_calls: set[str] = set()
        self._skill_invocations: dict[str, dict[str, str]] = {}
        self._usage: dict[str, int] = {}
        self._usage_ordinals: set[int] = set()

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    def project(self, event: Event) -> None:
        message = event.msg
        if isinstance(message, TurnStarted):
            for invocation in message.skill_invocations:
                self._skill_invocations[invocation.skill_id] = invocation.to_metadata()
            self._persist_skill_invocations()
        elif isinstance(message, SkillLoaded):
            invocation = message.invocation
            if invocation.skill_id not in self._skill_invocations:
                self._skill_invocations[invocation.skill_id] = invocation.to_metadata()
                self._persist_skill_invocations()
        elif isinstance(message, AgentMessageDelta):
            self._append_assistant_delta(message.delta, message.message_id)
        elif isinstance(message, AgentMessageCompleted):
            self._complete_assistant(
                message.text,
                message_id=message.message_id,
                phase=message.phase,
            )
        elif isinstance(message, AgentReasoningStarted):
            self._start_reasoning(
                message.reasoning_id,
                effort=message.effort,
            )
        elif isinstance(message, AgentReasoningDelta):
            self._append_reasoning_delta(
                message.reasoning_id,
                channel=message.channel,
                delta=message.delta,
            )
        elif isinstance(message, AgentReasoningCompleted):
            self._complete_reasoning(message)
        elif isinstance(message, AgentReasoningSummary):
            payload = ReasoningPayload(
                summary_text=message.text,
                streaming=False,
            )
            self._add_item(
                kind=ItemKind.REASONING,
                status=ItemStatus.COMPLETED,
                summary="Thinking",
                payload=payload.to_dict(),
            )
        elif isinstance(message, ModelUsageRecorded):
            self._record_usage(message)
        elif isinstance(message, AgentMessage):
            self.final_text = message.text
            self._complete_assistant(
                message.text,
                message_id=message.message_id,
                phase=message.phase,
            )
        elif isinstance(message, PlanUpdated):
            self._persist_plan_update(message)
        elif isinstance(message, ToolStarted):
            if (
                message.name.lower() in {"update_plan", "plan"}
                or message.activity is not None
                and message.activity.kind is ToolActivityKind.PLAN
            ):
                self._plan_tool_calls.add(message.call_id)
                return
            activity = (
                {
                    "kind": message.activity.kind.value,
                    "label": message.activity.label,
                    "subject": message.activity.subject,
                }
                if message.activity is not None
                else None
            )
            item = self._add_item(
                kind=self._kind_for_tool(message.name),
                status=ItemStatus.IN_PROGRESS,
                summary=(
                    message.activity.subject
                    if message.activity is not None and message.activity.subject
                    else message.detail or message.name
                ),
                payload={
                    "callId": message.call_id,
                    "name": message.name,
                    "detail": message.detail,
                    "activity": activity,
                },
            )
            self._tool_item_ids[message.call_id] = item.id
        elif isinstance(message, ToolCompleted):
            if message.call_id in self._plan_tool_calls:
                self._plan_tool_calls.discard(message.call_id)
                if message.is_error:
                    self._add_item(
                        kind=ItemKind.ERROR,
                        status=ItemStatus.FAILED,
                        summary="Plan update failed",
                        payload={
                            "name": message.name,
                            "resultPreview": message.result_preview,
                        },
                    )
                return
            item_id = self._tool_item_ids.get(message.call_id)
            if item_id is not None:
                self._update_item(
                    item_id,
                    status=ItemStatus.FAILED
                    if message.is_error
                    else ItemStatus.COMPLETED,
                    payload_update={
                        "isError": message.is_error,
                        "resultPreview": message.result_preview,
                    },
                )
        elif isinstance(message, ErrorEvent):
            self._add_item(
                kind=ItemKind.ERROR,
                status=ItemStatus.FAILED,
                summary=message.message,
                payload={"message": message.message},
            )
        elif isinstance(message, TaskComplete):
            self.saw_terminal = True
            self.stop_reason = message.stop_reason
            if message.final_text:
                self.final_text = message.final_text
                if not self._saw_final_assistant:
                    self._complete_assistant(
                        message.final_text,
                        phase=AgentMessagePhase.FINAL_ANSWER,
                    )

    def _record_usage(self, message: ModelUsageRecorded) -> None:
        ordinal = message.response_ordinal
        usage = normalize_usage(message.usage)
        if ordinal < 1 or ordinal in self._usage_ordinals or not usage:
            return
        with self.database.transaction() as connection:
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                type=TURN_USAGE_EVENT_TYPE,
                payload={
                    "responseOrdinal": ordinal,
                    "usage": usage,
                },
            )
        self._usage_ordinals.add(ordinal)
        add_usage(self._usage, usage)
        self.broker.publish(event)

    def _persist_skill_invocations(self) -> None:
        """Attach the auditable Skill ledger to the existing user message."""

        with self.database.transaction() as connection:
            repository = ItemRepository(connection)
            user_item = next(
                (
                    item
                    for item in repository.list_for_turn(self.turn_id)
                    if item.kind is ItemKind.USER_MESSAGE
                ),
                None,
            )
            if user_item is None:
                return
            skills = list(self._skill_invocations.values())
            if user_item.payload.get("skills") == skills:
                return
            updated = replace(
                user_item,
                payload={**user_item.payload, "skills": skills},
                updated_at=utc_now(),
            )
            repository.update(updated)
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                item_id=updated.id,
                type="item.updated",
                payload={"item": item_view(updated)},
            )
        self.broker.publish(event)

    def _persist_plan_update(self, update: PlanUpdated) -> None:
        """Store plan state as a Turn event, never as a transcript Item."""

        payload = {
            "plan": {
                "explanation": update.explanation,
                "steps": [
                    {"step": item.step, "status": item.status.value}
                    for item in update.plan
                ],
            }
        }
        with self.database.transaction() as connection:
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                type="turn.plan.updated",
                payload=payload,
            )
        self.broker.publish(event)

    def close_open_items(self, *, interrupted: bool) -> None:
        """Ensure an interrupted/failed turn leaves no misleading live cards."""

        with self.database.transaction() as connection:
            items = ItemRepository(connection)
            active = items.list_active_for_turn(self.turn_id)
            event_repo = EventRepository(connection)
            events: list[DomainEvent] = []
            for item in active:
                updated = replace(
                    item,
                    status=ItemStatus.DECLINED
                    if item.kind is ItemKind.APPROVAL_REQUEST
                    else ItemStatus.FAILED,
                    payload={**item.payload, "interrupted": interrupted},
                    updated_at=utc_now(),
                )
                items.update(updated)
                events.append(
                    event_repo.append(
                        thread_id=self.thread_id,
                        turn_id=self.turn_id,
                        item_id=updated.id,
                        type="item.updated",
                        payload={"item": item_view(updated)},
                    )
                )
        self._publish(events)

    def add_completion(
        self,
        *,
        status: ItemStatus,
        stop_reason: str,
        usage: dict[str, int] | None = None,
    ) -> Item:
        return self._add_item(
            kind=ItemKind.COMPLETION,
            status=status,
            summary=f"Turn {stop_reason.replace('_', ' ')}",
            payload={
                "stopReason": stop_reason,
                **({"usage": dict(usage)} if usage else {}),
            },
        )

    def add_error(self, *, code: str, message: str) -> Item:
        return self._add_item(
            kind=ItemKind.ERROR,
            status=ItemStatus.FAILED,
            summary=message,
            payload={"code": code, "message": message},
        )

    @staticmethod
    def _assistant_key(message_id: str | None) -> str:
        return message_id or "__legacy_assistant__"

    def _append_assistant_delta(
        self,
        delta: str,
        message_id: str | None,
    ) -> None:
        if not delta:
            return
        key = self._assistant_key(message_id)
        text = self._assistant_texts.get(key, "") + delta
        self._assistant_texts[key] = text
        item_id = self._assistant_item_ids.get(key)
        if item_id is None:
            item = self._add_item(
                kind=ItemKind.ASSISTANT_MESSAGE,
                status=ItemStatus.IN_PROGRESS,
                summary=text[:160],
                payload={
                    "text": text,
                    "streaming": True,
                    "messageId": message_id,
                    "phase": AgentMessagePhase.UNKNOWN.value,
                },
            )
            self._assistant_item_ids[key] = item.id
            self._last_delta_flush[key] = monotonic()
            self._last_flushed_length[key] = len(text)
            return
        now = monotonic()
        if (
            now - self._last_delta_flush.get(key, 0.0) < _STREAM_FLUSH_INTERVAL_S
            and len(text) - self._last_flushed_length.get(key, 0)
            < _STREAM_FLUSH_MIN_CHARS
        ):
            return
        pending_delta = text[self._last_flushed_length.get(key, 0) :]
        updated = self._append_item_delta(
            item_id,
            delta=pending_delta,
            summary=text[:160],
        )
        if updated is None:
            return
        self._last_delta_flush[key] = now
        self._last_flushed_length[key] = len(text)

    def _complete_assistant(
        self,
        text: str,
        *,
        message_id: str | None = None,
        phase: AgentMessagePhase = AgentMessagePhase.FINAL_ANSWER,
    ) -> None:
        key = self._assistant_key(message_id)
        self._assistant_texts[key] = text
        item_id = self._assistant_item_ids.get(key)
        payload = {
            "text": text,
            "streaming": False,
            "messageId": message_id,
            "phase": phase.value,
        }
        if item_id is None:
            item = self._add_item(
                kind=ItemKind.ASSISTANT_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=text[:160],
                payload=payload,
            )
            self._assistant_item_ids[key] = item.id
        else:
            self._update_item(
                item_id,
                status=ItemStatus.COMPLETED,
                summary=text[:160],
                payload_update=payload,
            )
        self._last_flushed_length[key] = len(text)
        if phase is AgentMessagePhase.FINAL_ANSWER:
            self._saw_final_assistant = True

    def _start_reasoning(
        self,
        reasoning_id: str,
        *,
        effort: str | None,
    ) -> Item:
        item_id = self._reasoning_item_ids.get(reasoning_id)
        if item_id is not None:
            with self.database.read() as connection:
                existing = ItemRepository(connection).get(item_id)
            if existing is not None:
                return existing
        payload = ReasoningPayload(effort=effort, streaming=True)
        item = self._add_item(
            kind=ItemKind.REASONING,
            status=ItemStatus.IN_PROGRESS,
            summary="Thinking",
            payload=payload.to_dict(),
        )
        self._reasoning_item_ids[reasoning_id] = item.id
        self._reasoning_payloads[reasoning_id] = payload
        return item

    def _append_reasoning_delta(
        self,
        reasoning_id: str,
        *,
        channel: ReasoningChannel,
        delta: str,
    ) -> None:
        if not delta:
            return
        if reasoning_id not in self._reasoning_item_ids:
            self._start_reasoning(reasoning_id, effort=None)
        payload = self._reasoning_payloads.get(reasoning_id, ReasoningPayload())
        payload = payload.with_delta(channel, delta)
        self._reasoning_payloads[reasoning_id] = payload
        key = (reasoning_id, channel)
        text = payload.text_for(channel)
        now = monotonic()
        if (
            now - self._reasoning_last_flush.get(key, 0.0) < _STREAM_FLUSH_INTERVAL_S
            and len(text) - self._reasoning_last_length.get(key, 0)
            < _STREAM_FLUSH_MIN_CHARS
        ):
            return
        pending = text[self._reasoning_last_length.get(key, 0) :]
        item_id = self._reasoning_item_ids[reasoning_id]
        updated = self._append_reasoning_item_delta(
            item_id,
            channel=channel,
            delta=pending,
        )
        if updated is None:
            return
        self._reasoning_last_flush[key] = now
        self._reasoning_last_length[key] = len(text)

    def _complete_reasoning(self, message: AgentReasoningCompleted) -> None:
        if message.reasoning_id not in self._reasoning_item_ids:
            self._start_reasoning(
                message.reasoning_id,
                effort=message.effort,
            )
        payload = ReasoningPayload(
            summary_text=message.summary_text,
            trace_text=message.trace_text,
            availability=message.availability,
            effort=message.effort,
            duration_ms=message.duration_ms,
            streaming=False,
        )
        self._reasoning_payloads[message.reasoning_id] = payload
        item_id = self._reasoning_item_ids[message.reasoning_id]
        self._update_item(
            item_id,
            status=ItemStatus.COMPLETED,
            summary="Thinking",
            payload_update=payload.to_dict(),
        )
        for channel in ReasoningChannel:
            key = (message.reasoning_id, channel)
            self._reasoning_last_length[key] = len(payload.text_for(channel))

    def _add_item(
        self,
        *,
        kind: ItemKind,
        status: ItemStatus,
        summary: str,
        payload: dict,
    ) -> Item:
        with self.database.transaction() as connection:
            repository = ItemRepository(connection)
            now = utc_now()
            item = Item(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                ordinal=repository.next_ordinal(self.turn_id),
                kind=kind,
                status=status,
                summary=summary,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            repository.add(item)
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                item_id=item.id,
                type="item.created",
                payload={"item": item_view(item)},
            )
        self.broker.publish(event)
        return item

    def _update_item(
        self,
        item_id: str,
        *,
        status: ItemStatus,
        payload_update: dict,
        summary: str | None = None,
    ) -> Item | None:
        with self.database.transaction() as connection:
            repository = ItemRepository(connection)
            item = repository.get(item_id)
            if item is None:
                return None
            updated = replace(
                item,
                status=status,
                summary=summary if summary is not None else item.summary,
                payload={**item.payload, **payload_update},
                updated_at=utc_now(),
            )
            repository.update(updated)
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                item_id=updated.id,
                type="item.updated",
                payload={"item": item_view(updated)},
            )
        self.broker.publish(event)
        return updated

    def _append_item_delta(
        self,
        item_id: str,
        *,
        delta: str,
        summary: str,
    ) -> Item | None:
        """Persist current item state while logging only newly appended text."""

        if not delta:
            return None
        with self.database.transaction() as connection:
            repository = ItemRepository(connection)
            item = repository.get(item_id)
            if item is None:
                return None
            text = item.payload.get("text")
            current_text = text if isinstance(text, str) else ""
            updated = replace(
                item,
                status=ItemStatus.IN_PROGRESS,
                summary=summary,
                payload={
                    **item.payload,
                    "text": current_text + delta,
                    "streaming": True,
                },
                updated_at=utc_now(),
            )
            repository.update(updated)
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                item_id=updated.id,
                type="item.delta",
                payload={
                    "delta": delta,
                    "summary": updated.summary,
                    "streaming": True,
                    "updatedAt": timestamp(updated.updated_at),
                },
            )
        self.broker.publish(event)
        return updated

    def _append_reasoning_item_delta(
        self,
        item_id: str,
        *,
        channel: ReasoningChannel,
        delta: str,
    ) -> Item | None:
        """Persist and publish one typed reasoning delta."""

        if not delta:
            return None
        with self.database.transaction() as connection:
            repository = ItemRepository(connection)
            item = repository.get(item_id)
            if item is None:
                return None
            payload = ReasoningPayload.from_dict(item.payload).with_delta(
                channel,
                delta,
            )
            updated = replace(
                item,
                status=ItemStatus.IN_PROGRESS,
                payload=payload.to_dict(),
                updated_at=utc_now(),
            )
            repository.update(updated)
            event = EventRepository(connection).append(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                item_id=updated.id,
                type="item.delta",
                payload={
                    "delta": delta,
                    "reasoningChannel": channel.value,
                    "streaming": True,
                    "updatedAt": timestamp(updated.updated_at),
                },
            )
        self.broker.publish(event)
        return updated

    def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.broker.publish(event)

    @staticmethod
    def _kind_for_tool(tool_name: str) -> ItemKind:
        lowered = tool_name.lower()
        if lowered in {"update_plan", "plan"}:
            return ItemKind.PLAN
        if lowered in {"bash", "exec", "execute_bash", "execute_commands"}:
            return ItemKind.COMMAND_EXECUTION
        if any(token in lowered for token in ("write", "edit", "apply_patch")):
            return ItemKind.FILE_CHANGE
        return ItemKind.TOOL_CALL
