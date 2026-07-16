"""Durable approval state machine bridging JSON-RPC and the agent loop."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, replace
from typing import Any

from core.application.errors import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    ConflictError,
)
from core.application.event_service import EventBroker
from core.application.views import approval_view, item_view, thread_view, turn_view
from core.domain.approval import Approval, ApprovalCategory, ApprovalStatus
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.thread import ThreadStatus
from core.domain.turn import TurnStatus
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import (
    ApprovalRepository,
    ItemRepository,
    TurnRepository,
)
from core.persistence.thread_repository import ThreadRepository


@dataclass(slots=True)
class _PendingDecision:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[ApprovalStatus]


class ApprovalService:
    """Create, resolve, cancel, and resume permission-gated tool calls."""

    _ALLOWED_DECISIONS = {
        ApprovalStatus.APPROVED_ONCE,
        ApprovalStatus.APPROVED_SESSION,
        ApprovalStatus.DENIED,
    }

    def __init__(self, database: Database, broker: EventBroker) -> None:
        self.database = database
        self.broker = broker
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingDecision] = {}
        self._session_grants: set[tuple[str, str]] = set()

    async def request(
        self,
        *,
        thread_id: str,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str | None,
    ) -> bool:
        with self._lock:
            if (thread_id, tool_name) in self._session_grants:
                return True

        loop = asyncio.get_running_loop()
        decision_future: asyncio.Future[ApprovalStatus] = loop.create_future()
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            turn = turns.get(turn_id)
            if turn is None or turn.thread_id != thread_id or turn.status.is_terminal:
                return False
            threads = ThreadRepository(connection)
            thread = threads.get(thread_id)
            if thread is None:
                return False
            items = ItemRepository(connection)
            now = utc_now()
            item = Item(
                thread_id=thread_id,
                turn_id=turn_id,
                ordinal=items.next_ordinal(turn_id),
                kind=ItemKind.APPROVAL_REQUEST,
                status=ItemStatus.PENDING,
                summary=f"Approval required: {tool_name}",
                payload={
                    "toolName": tool_name,
                    "arguments": arguments,
                    "reason": reason,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(item)
            approval = Approval(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item.id,
                category=self._category(tool_name, arguments),
                request={
                    "toolName": tool_name,
                    "arguments": arguments,
                    "reason": reason,
                },
                requested_at=now,
            )
            ApprovalRepository(connection).add(approval)
            waiting_turn = replace(turn, status=TurnStatus.WAITING_APPROVAL)
            turns.update(waiting_turn)
            waiting_thread = replace(
                thread,
                status=ThreadStatus.WAITING,
                updated_at=now,
            )
            threads.update(waiting_thread)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_id=item.id,
                        type="item.created",
                        payload={"item": item_view(item)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        item_id=item.id,
                        type="approval.requested",
                        payload={"approval": approval_view(approval)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        turn_id=turn_id,
                        type="turn.updated",
                        payload={"turn": turn_view(waiting_turn)},
                    ),
                    event_repo.append(
                        thread_id=thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(waiting_thread)},
                    ),
                )
            )

        with self._lock:
            self._pending[approval.id] = _PendingDecision(loop, decision_future)
        self._publish(events)
        try:
            decision = await decision_future
        except asyncio.CancelledError:
            self._cancel(approval.id, reason="turn_interrupted")
            raise
        finally:
            with self._lock:
                self._pending.pop(approval.id, None)

        if decision is ApprovalStatus.APPROVED_SESSION:
            with self._lock:
                self._session_grants.add((thread_id, tool_name))
        return decision in {
            ApprovalStatus.APPROVED_ONCE,
            ApprovalStatus.APPROVED_SESSION,
        }

    def respond(
        self,
        approval_id: str,
        *,
        decision: ApprovalStatus,
        message: str | None = None,
    ) -> Approval:
        if decision not in self._ALLOWED_DECISIONS:
            raise ConflictError(f"unsupported approval decision: {decision.value}")
        with self.database.read() as connection:
            existing = ApprovalRepository(connection).get(approval_id)
        if existing is None:
            raise ApprovalNotFoundError(f"approval not found: {approval_id}")
        if existing.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyResolvedError(
                f"approval is already resolved: {approval_id}"
            )
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            raise ApprovalAlreadyResolvedError(
                "approval is no longer attached to a live turn"
            )
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            approvals = ApprovalRepository(connection)
            current = approvals.get(approval_id)
            if current is None:
                raise ApprovalNotFoundError(f"approval not found: {approval_id}")
            if current.status is not ApprovalStatus.PENDING:
                raise ApprovalAlreadyResolvedError(
                    f"approval is already resolved: {approval_id}"
                )
            now = utc_now()
            resolved = replace(
                current,
                status=decision,
                decision={"status": decision.value, "message": message},
                resolved_at=now,
            )
            approvals.update(resolved)
            items = ItemRepository(connection)
            item = items.get(current.item_id)
            if item is None:
                raise ConflictError("approval item is missing")
            approved = decision in {
                ApprovalStatus.APPROVED_ONCE,
                ApprovalStatus.APPROVED_SESSION,
            }
            resolved_item = replace(
                item,
                status=ItemStatus.COMPLETED if approved else ItemStatus.DECLINED,
                summary=("Approved: " if approved else "Denied: ")
                + str(current.request.get("toolName", "tool")),
                payload={**item.payload, "decision": resolved.decision},
                updated_at=now,
            )
            items.update(resolved_item)
            turns = TurnRepository(connection)
            turn = turns.get(current.turn_id)
            if turn is None or turn.status is not TurnStatus.WAITING_APPROVAL:
                raise ConflictError("approval no longer belongs to a waiting turn")
            running_turn = replace(turn, status=TurnStatus.RUNNING)
            turns.update(running_turn)
            threads = ThreadRepository(connection)
            thread = threads.get(current.thread_id)
            if thread is None:
                raise ConflictError("approval thread is missing")
            running_thread = replace(
                thread, status=ThreadStatus.RUNNING, updated_at=now
            )
            threads.update(running_thread)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=current.turn_id,
                        item_id=current.item_id,
                        type="approval.resolved",
                        payload={"approval": approval_view(resolved)},
                    ),
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=current.turn_id,
                        item_id=current.item_id,
                        type="item.updated",
                        payload={"item": item_view(resolved_item)},
                    ),
                    event_repo.append(
                        thread_id=current.thread_id,
                        turn_id=current.turn_id,
                        type="turn.updated",
                        payload={"turn": turn_view(running_turn)},
                    ),
                    event_repo.append(
                        thread_id=current.thread_id,
                        type="thread.status_changed",
                        payload={"thread": thread_view(running_thread)},
                    ),
                )
            )

        self._publish(events)
        pending.loop.call_soon_threadsafe(
            self._set_result_if_pending, pending.future, decision
        )
        return resolved

    def _cancel(self, approval_id: str, *, reason: str) -> None:
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            approvals = ApprovalRepository(connection)
            current = approvals.get(approval_id)
            if current is None or current.status is not ApprovalStatus.PENDING:
                return
            now = utc_now()
            cancelled = replace(
                current,
                status=ApprovalStatus.CANCELLED,
                decision={"status": ApprovalStatus.CANCELLED.value, "reason": reason},
                resolved_at=now,
            )
            approvals.update(cancelled)
            items = ItemRepository(connection)
            item = items.get(current.item_id)
            if item is not None:
                cancelled_item = replace(
                    item,
                    status=ItemStatus.DECLINED,
                    payload={**item.payload, "decision": cancelled.decision},
                    updated_at=now,
                )
                items.update(cancelled_item)
            event = EventRepository(connection).append(
                thread_id=current.thread_id,
                turn_id=current.turn_id,
                item_id=current.item_id,
                type="approval.resolved",
                payload={"approval": approval_view(cancelled)},
            )
            events.append(event)
        self._publish(events)

    @staticmethod
    def _category(tool_name: str, arguments: dict[str, Any]) -> ApprovalCategory:
        lowered = tool_name.lower()
        command = str(arguments.get("command", "")).lower()
        if any(token in command for token in (" rm ", "rm -", "git reset", "drop ")):
            return ApprovalCategory.DESTRUCTIVE
        if lowered in {"bash", "exec", "execute_commands"}:
            return ApprovalCategory.COMMAND
        if any(token in lowered for token in ("write", "edit", "patch")):
            return ApprovalCategory.FILE_WRITE
        if any(token in lowered for token in ("web", "network", "fetch")):
            return ApprovalCategory.NETWORK
        return ApprovalCategory.EXTERNAL_TOOL

    def _publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            self.broker.publish(event)

    @staticmethod
    def _set_result_if_pending(
        future: asyncio.Future[ApprovalStatus], decision: ApprovalStatus
    ) -> None:
        if not future.done():
            future.set_result(decision)
