"""Projection of live SQ/EQ events into durable timeline items."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from core.application.event_service import EventBroker
from core.application.views import item_view, timestamp
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.events import (
    AgentMessage,
    AgentMessageDelta,
    ErrorEvent,
    Event,
    TaskComplete,
    ToolCompleted,
    ToolStarted,
)
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository


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
        self._assistant_item_id: str | None = None
        self._assistant_text = ""
        self._last_delta_flush = 0.0
        self._last_flushed_length = 0
        self._tool_item_ids: dict[str, str] = {}

    def project(self, event: Event) -> None:
        message = event.msg
        if isinstance(message, AgentMessageDelta):
            self._append_assistant_delta(message.delta)
        elif isinstance(message, AgentMessage):
            self.final_text = message.text
            self._complete_assistant(message.text)
        elif isinstance(message, ToolStarted):
            item = self._add_item(
                kind=self._kind_for_tool(message.name),
                status=ItemStatus.IN_PROGRESS,
                summary=message.detail or message.name,
                payload={
                    "callId": message.call_id,
                    "name": message.name,
                    "detail": message.detail,
                },
            )
            self._tool_item_ids[message.call_id] = item.id
        elif isinstance(message, ToolCompleted):
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
                if self._assistant_item_id is None:
                    self._complete_assistant(message.final_text)

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

    def add_completion(self, *, status: ItemStatus, stop_reason: str) -> Item:
        return self._add_item(
            kind=ItemKind.COMPLETION,
            status=status,
            summary=f"Turn {stop_reason.replace('_', ' ')}",
            payload={"stopReason": stop_reason},
        )

    def add_error(self, *, code: str, message: str) -> Item:
        return self._add_item(
            kind=ItemKind.ERROR,
            status=ItemStatus.FAILED,
            summary=message,
            payload={"code": code, "message": message},
        )

    def _append_assistant_delta(self, delta: str) -> None:
        if not delta:
            return
        self._assistant_text += delta
        if self._assistant_item_id is None:
            item = self._add_item(
                kind=ItemKind.ASSISTANT_MESSAGE,
                status=ItemStatus.IN_PROGRESS,
                summary=self._assistant_text[:160],
                payload={"text": self._assistant_text, "streaming": True},
            )
            self._assistant_item_id = item.id
            self._last_delta_flush = monotonic()
            self._last_flushed_length = len(self._assistant_text)
            return
        now = monotonic()
        if (
            now - self._last_delta_flush < 0.05
            and len(self._assistant_text) - self._last_flushed_length < 256
        ):
            return
        pending_delta = self._assistant_text[self._last_flushed_length :]
        updated = self._append_item_delta(
            self._assistant_item_id,
            delta=pending_delta,
            summary=self._assistant_text[:160],
        )
        if updated is None:
            return
        self._last_delta_flush = now
        self._last_flushed_length = len(self._assistant_text)

    def _complete_assistant(self, text: str) -> None:
        self._assistant_text = text
        if self._assistant_item_id is None:
            item = self._add_item(
                kind=ItemKind.ASSISTANT_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=text[:160],
                payload={"text": text, "streaming": False},
            )
            self._assistant_item_id = item.id
            return
        self._update_item(
            self._assistant_item_id,
            status=ItemStatus.COMPLETED,
            summary=text[:160],
            payload_update={"text": text, "streaming": False},
        )

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
