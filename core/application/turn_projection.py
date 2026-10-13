"""Projection of live SQ/EQ events into durable timeline items."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import TypeVar

from core.application.event_service import EventBroker
from core.application.live_items import TEXT_CHANNEL, LiveItems
from core.application.turn_usage import (
    TURN_USAGE_EVENT_TYPE,
    add_usage,
    normalize_usage,
)
from core.application.views import item_view
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

# Streamed text reaches clients in batches of at most this age or size.
_STREAM_FLUSH_INTERVAL_S = 0.05
_STREAM_FLUSH_MIN_CHARS = 256
# Clients get streamed text live, not from the item's row. The row is brought
# up to date this often, which bounds what a crash of the service loses.
_CHECKPOINT_INTERVAL_S = 5.0
_INCOMPLETE_NOTICE = (
    "Some updates in this turn could not be saved, so its transcript may be "
    "incomplete. The service log has the details."
)

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class TurnEventProjector:
    """Map live events into the rebuildable Desktop execution projection.

    Items, their completion and the Turn's other changes are written to the
    database and its event log. Streamed text is not: it goes to clients as
    live deltas and waits in ``live_items`` until its item completes.

    A write the database refuses is logged and costs only what clients see,
    and the Turn goes on. None of the public methods raise for a failed write.
    """

    def __init__(
        self,
        database: Database,
        broker: EventBroker,
        *,
        live_items: LiveItems,
        thread_id: str,
        turn_id: str,
    ) -> None:
        self.database = database
        self.broker = broker
        self.live = live_items
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.final_text: str | None = None
        self.stop_reason: str | None = None
        self.saw_terminal = False
        self.failed_writes = 0
        self._final_message_id: str | None = None
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
        self._checkpointed: dict[str, float] = {}
        self._trailing_flush: asyncio.TimerHandle | None = None

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    def project(self, event: Event) -> None:
        message = event.msg
        # The Turn's outcome is noted before anything is written, so a failed
        # write can never change it.
        if isinstance(message, AgentMessage):
            self.final_text = message.text
        elif isinstance(message, TaskComplete):
            self.saw_terminal = True
            self.stop_reason = message.stop_reason
            if message.final_text:
                self.final_text = message.final_text
        if (
            isinstance(message, AgentMessage | AgentMessageCompleted)
            and message.phase is AgentMessagePhase.FINAL_ANSWER
        ):
            self._final_message_id = message.message_id
        # Throttled text must reach clients before anything that follows it,
        # or a client sees a completion, tool card or other stream before the
        # end of the text it already started showing.
        if isinstance(message, AgentMessageDelta):
            self._guarded(self._flush_reasoning_streams)
        elif isinstance(message, AgentReasoningDelta):
            self._guarded(self._flush_assistant_streams)
        else:
            self._guarded(self._flush_pending_streams)
        self._guarded(self._write, message)

    def _write(self, message: object) -> None:
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
            if message.warning:
                # A problem the Turn survived: clients show an error item with
                # this severity as a warning, not as a failed step.
                self._add_item(
                    kind=ItemKind.ERROR,
                    status=ItemStatus.COMPLETED,
                    summary=message.message,
                    payload={"message": message.message, "severity": "warning"},
                )
            else:
                self._add_item(
                    kind=ItemKind.ERROR,
                    status=ItemStatus.FAILED,
                    summary=message.message,
                    payload={"message": message.message},
                )
        elif isinstance(message, TaskComplete):
            if message.final_text and not self._saw_final_assistant:
                # Completes the answer's own item when its write failed, and
                # adds one for a session that never streamed it.
                self._complete_assistant(
                    message.final_text,
                    message_id=self._final_message_id,
                    phase=AgentMessagePhase.FINAL_ANSWER,
                )

    def _guarded(self, write: Callable[..., _T], *args, **kwargs) -> _T | None:
        try:
            return write(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a lost update must not end the Turn
            self.failed_writes += 1
            if self.failed_writes == 1:
                logger.exception("could not save an update of turn %s", self.turn_id)
            else:
                logger.warning(
                    "could not save an update of turn %s: %s: %s",
                    self.turn_id,
                    type(exc).__name__,
                    exc,
                )
            return None

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

        # Persist text that was streamed but still held by the throttle, so the
        # closed item keeps everything the model produced before it stopped.
        self._guarded(self._flush_pending_streams)
        self._guarded(self._close_open_items, interrupted)

    def _close_open_items(self, interrupted: bool) -> None:
        with self.database.transaction() as connection:
            items = ItemRepository(connection)
            # Streamed text is newer than the rows; keep all of it.
            active = [
                self.live.overlay(item)
                for item in items.list_active_for_turn(self.turn_id)
            ]
            event_repo = EventRepository(connection)
            events: list[DomainEvent] = []
            for item in active:
                payload = {**item.payload, "interrupted": interrupted}
                if "streaming" in payload:
                    payload["streaming"] = False
                updated = replace(
                    item,
                    status=ItemStatus.DECLINED
                    if item.kind is ItemKind.APPROVAL_REQUEST
                    else ItemStatus.FAILED,
                    payload=payload,
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
        for item in active:
            self.live.finish(item.id)

    def add_completion(
        self,
        *,
        status: ItemStatus,
        stop_reason: str,
        usage: dict[str, int] | None = None,
    ) -> Item | None:
        return self._guarded(
            self._add_item,
            kind=ItemKind.COMPLETION,
            status=status,
            summary=f"Turn {stop_reason.replace('_', ' ')}",
            payload={
                "stopReason": stop_reason,
                **({"usage": dict(usage)} if usage else {}),
            },
        )

    def add_error(self, *, code: str, message: str) -> Item | None:
        return self._guarded(
            self._add_item,
            kind=ItemKind.ERROR,
            status=ItemStatus.FAILED,
            summary=message,
            payload={"code": code, "message": message},
        )

    def add_incomplete_notice(self) -> None:
        """Tell clients this Turn's transcript lost some of its updates.

        A warning, not an error: the Turn itself may well have succeeded.
        """

        if self.failed_writes:
            self._guarded(
                self._add_item,
                kind=ItemKind.ERROR,
                status=ItemStatus.COMPLETED,
                summary=_INCOMPLETE_NOTICE,
                payload={
                    "code": "UPDATES_NOT_SAVED",
                    "message": _INCOMPLETE_NOTICE,
                    "severity": "warning",
                },
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
            self._follow(item, {TEXT_CHANNEL: text})
            self._last_delta_flush[key] = monotonic()
            self._last_flushed_length[key] = len(text)
            return
        if self._held(
            self._last_delta_flush.get(key, 0.0),
            len(text) - self._last_flushed_length.get(key, 0),
        ):
            return
        self._flush_assistant_stream(key)

    def _complete_assistant(
        self,
        text: str,
        *,
        message_id: str | None = None,
        phase: AgentMessagePhase = AgentMessagePhase.FINAL_ANSWER,
    ) -> None:
        key = self._assistant_key(message_id)
        # Deliver the throttled tail as a delta first: a client that printed
        # the stream can then match the completed text instead of reprinting.
        self._guarded(self._flush_assistant_stream, key)
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
        elif (
            self._update_item(
                item_id,
                status=ItemStatus.COMPLETED,
                summary=text[:160],
                payload_update=payload,
            )
            is not None
        ):
            self.live.finish(item_id)
        self._last_flushed_length[key] = len(text)
        if phase is AgentMessagePhase.FINAL_ANSWER:
            self._saw_final_assistant = True

    def _flush_assistant_stream(self, key: str) -> None:
        """Send assistant text the throttle is still holding, if any."""

        item_id = self._assistant_item_ids.get(key)
        if item_id is None:
            return
        text = self._assistant_texts.get(key, "")
        flushed = self._last_flushed_length.get(key, 0)
        if len(text) <= flushed:
            return
        self._last_delta_flush[key] = monotonic()
        self._last_flushed_length[key] = len(text)
        self._publish_live(
            item_id,
            channel=TEXT_CHANNEL,
            delta=text[flushed:],
            payload_update={"text": text},
            summary=text[:160],
        )

    def _flush_assistant_streams(self) -> None:
        for key in tuple(self._assistant_item_ids):
            self._flush_assistant_stream(key)

    def _flush_reasoning_stream(self, reasoning_id: str) -> None:
        """Send reasoning text the throttle is still holding, per channel."""

        item_id = self._reasoning_item_ids.get(reasoning_id)
        payload = self._reasoning_payloads.get(reasoning_id)
        if item_id is None or payload is None:
            return
        for channel in ReasoningChannel:
            key = (reasoning_id, channel)
            text = payload.text_for(channel)
            flushed = self._reasoning_last_length.get(key, 0)
            if len(text) <= flushed:
                continue
            self._reasoning_last_flush[key] = monotonic()
            self._reasoning_last_length[key] = len(text)
            self._publish_live(
                item_id,
                channel=channel.value,
                delta=text[flushed:],
                payload_update=self._sent_reasoning(reasoning_id).to_dict(),
            )

    def _sent_reasoning(self, reasoning_id: str) -> ReasoningPayload:
        """The reasoning payload as far as clients have received it."""

        payload = self._reasoning_payloads[reasoning_id]
        summary, trace = (
            self._reasoning_last_length.get((reasoning_id, channel), 0)
            for channel in (ReasoningChannel.SUMMARY, ReasoningChannel.PROVIDER_TRACE)
        )
        return replace(
            payload,
            summary_text=payload.summary_text[:summary],
            trace_text=payload.trace_text[:trace],
        )

    def _flush_reasoning_streams(self) -> None:
        for reasoning_id in tuple(self._reasoning_item_ids):
            self._flush_reasoning_stream(reasoning_id)

    def _flush_pending_streams(self) -> None:
        self._flush_assistant_streams()
        self._flush_reasoning_streams()

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
        self._follow(item, {channel.value: "" for channel in ReasoningChannel})
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
        if self._held(
            self._reasoning_last_flush.get(key, 0.0),
            len(payload.text_for(channel)) - self._reasoning_last_length.get(key, 0),
        ):
            return
        self._flush_reasoning_stream(reasoning_id)

    def _complete_reasoning(self, message: AgentReasoningCompleted) -> None:
        if message.reasoning_id not in self._reasoning_item_ids:
            self._start_reasoning(
                message.reasoning_id,
                effort=message.effort,
            )
        self._guarded(self._flush_reasoning_stream, message.reasoning_id)
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
        if (
            self._update_item(
                item_id,
                status=ItemStatus.COMPLETED,
                summary="Thinking",
                payload_update=payload.to_dict(),
            )
            is not None
        ):
            self.live.finish(item_id)
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

    def _follow(self, item: Item, texts: dict[str, str]) -> None:
        """Stream ``item``'s text live from now on; its row has ``texts``."""

        self.live.begin(item, texts)
        self._checkpointed[item.id] = monotonic()

    def _held(self, last_flush: float, pending: int) -> bool:
        """Whether the throttle holds pending text back for now.

        Held text goes out with the next delta or, if the model pauses, once
        the interval has passed.
        """

        if (
            monotonic() - last_flush >= _STREAM_FLUSH_INTERVAL_S
            or pending >= _STREAM_FLUSH_MIN_CHARS
        ):
            return False
        if self._trailing_flush is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return False  # Nothing could send it later; send it now.
            self._trailing_flush = loop.call_later(
                _STREAM_FLUSH_INTERVAL_S, self._flush_held
            )
        return True

    def _flush_held(self) -> None:
        self._trailing_flush = None
        self._guarded(self._flush_pending_streams)

    def _publish_live(
        self,
        item_id: str,
        *,
        channel: str,
        delta: str,
        payload_update: dict,
        summary: str | None = None,
    ) -> None:
        live = self.live.append(
            item_id,
            channel=channel,
            delta=delta,
            payload_update=payload_update,
            summary=summary,
        )
        if live is None:
            return
        self.broker.publish_live(live)
        self._checkpoint(item_id)

    def _checkpoint(self, item_id: str) -> None:
        """Bring a streaming item's row up to date every few seconds.

        It never waits for a locked database, so a lock cannot stall the
        stream: a skipped checkpoint is caught up by the next one, and the
        item's completion writes all of its text regardless.
        """

        now = monotonic()
        if now - self._checkpointed.get(item_id, now) < _CHECKPOINT_INTERVAL_S:
            return
        self._checkpointed[item_id] = now
        try:
            with self.database.transaction(wait=False) as connection:
                repository = ItemRepository(connection)
                item = repository.get(item_id)
                if item is not None and item.status is ItemStatus.IN_PROGRESS:
                    repository.update(
                        replace(self.live.overlay(item), updated_at=utc_now())
                    )
        except sqlite3.Error as exc:
            logger.debug("skipped a checkpoint of item %s: %s", item_id, exc)

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
