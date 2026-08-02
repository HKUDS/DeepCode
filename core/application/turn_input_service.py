"""Strict input commands for one executing Turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.agent_runtime.injections import (
    GoalObjectiveUpdated,
    TurnInputCapacityError,
    TurnInputClosedError,
    TurnInputConflictError,
    TurnInputTargetError,
    TurnInputTooLargeError,
    UserSteer,
)
from core.application.errors import (
    DuplicateMessageConflictError,
    EmptyInputError,
    ExpectedTurnMismatchError,
    InputTooLargeError,
    NoActiveTurnError,
    ThreadNotFoundError,
    TurnInputBoundaryState,
    TurnInputCapacityExceededError,
    TurnNotSteerableError,
)
from core.application.session_runtime import SessionRuntimeRegistry
from core.application.views import item_view
from core.domain.common import utc_now
from core.domain.event import DomainEvent
from core.domain.item import Item, ItemKind, ItemStatus
from core.domain.message_provenance import (
    ClientSurface,
    TurnInputDelivery,
    TurnInputSource,
)
from core.domain.turn import Turn, TurnStatus
from core.persistence.database import Database
from core.persistence.event_repository import EventRepository
from core.persistence.execution_repository import ItemRepository, TurnRepository
from core.persistence.thread_repository import ThreadRepository
from core.sessions import SessionStore

EventPublisher = Callable[[list[DomainEvent]], None]


@dataclass(frozen=True, slots=True)
class TurnInputReceipt:
    message_id: str
    delivery: str
    turn: Turn
    duplicate: bool = False


class TurnInputService:
    """Persist and deliver input without starting or queueing another Turn."""

    def __init__(
        self,
        database: Database,
        session_runtimes: SessionRuntimeRegistry,
        session_store: SessionStore,
        publish: EventPublisher,
    ) -> None:
        self.database = database
        self.session_runtimes = session_runtimes
        self.session_store = session_store
        self._publish = publish

    def steer(
        self,
        thread_id: str,
        *,
        expected_turn_id: str,
        prompt: str,
        message_id: str,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> TurnInputReceipt:
        clean_prompt = prompt.strip()
        clean_expected = expected_turn_id.strip()
        clean_message_id = message_id.strip()
        if not clean_prompt:
            raise EmptyInputError("turn input must not be empty")
        if not clean_expected:
            raise ExpectedTurnMismatchError(clean_expected, None)
        if not clean_message_id:
            raise EmptyInputError("messageId must not be empty")

        duplicate = self._persisted_input(
            thread_id,
            prompt=clean_prompt,
            message_id=clean_message_id,
        )
        if duplicate is not None:
            return duplicate

        executing = self._input_target(thread_id)
        if executing is None:
            raise NoActiveTurnError(
                f"thread has no active Turn: {thread_id}",
                details={"threadId": thread_id, "actualTurnId": None},
            )
        if executing.id != clean_expected:
            raise ExpectedTurnMismatchError(clean_expected, executing.id)

        try:
            reservation = self.session_runtimes.reserve_input(
                thread_id,
                UserSteer(
                    message_id=clean_message_id,
                    target_turn_id=clean_expected,
                    text=clean_prompt,
                ),
            )
        except TurnInputTooLargeError as exc:
            raise InputTooLargeError(str(exc)) from exc
        except TurnInputCapacityError as exc:
            raise TurnInputCapacityExceededError(str(exc)) from exc
        except TurnInputConflictError as exc:
            raise DuplicateMessageConflictError(str(exc)) from exc
        except TurnInputTargetError as exc:
            raise ExpectedTurnMismatchError(
                clean_expected,
                exc.actual_turn_id,
            ) from exc
        except TurnInputClosedError as exc:
            raise TurnNotSteerableError(
                str(exc),
                state=TurnInputBoundaryState(exc.state.value),
                details={
                    "threadId": thread_id,
                    "expectedTurnId": clean_expected,
                },
            ) from exc

        if reservation is None:
            return TurnInputReceipt(
                message_id=clean_message_id,
                delivery=TurnInputDelivery.CURRENT_TURN.value,
                turn=executing,
                duplicate=True,
            )

        try:
            self._persist_live_input(
                executing,
                prompt=clean_prompt,
                message_id=clean_message_id,
                client_surface=client_surface,
            )
            self.session_runtimes.commit_input(thread_id, reservation)
        except BaseException:
            self.session_runtimes.cancel_input(thread_id, reservation)
            raise
        return TurnInputReceipt(
            message_id=clean_message_id,
            delivery=TurnInputDelivery.CURRENT_TURN.value,
            turn=executing,
        )

    def inject_goal_update(
        self,
        turn_id: str,
        *,
        message_id: str,
        goal_id: str,
        objective: str,
    ) -> bool:
        """Best-effort live notification; the Goal ledger remains authoritative."""

        with self.database.read() as connection:
            turn = TurnRepository(connection).get(turn_id)
        if turn is None or turn.status not in {
            TurnStatus.RUNNING,
            TurnStatus.WAITING_APPROVAL,
        }:
            return False
        return self.session_runtimes.inject_transient(
            turn.thread_id,
            GoalObjectiveUpdated(
                message_id=message_id,
                target_turn_id=turn.id,
                goal_id=goal_id,
                objective=objective,
            ),
        )

    def _input_target(self, thread_id: str) -> Turn | None:
        """Return an executing Turn or one already claimed for local startup.

        A claim-owning Turn remains durably ``queued`` until its execution
        coroutine marks it running. Its mailbox is prepared synchronously by
        the claim handler, so it is safe for a producer to wait on that
        ordering boundary. Unclaimed queued work is deliberately excluded.
        """

        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            turns = TurnRepository(connection)
            executing = turns.executing_for_thread(thread_id)
            if executing is not None:
                return executing
            active = turns.active_for_thread(thread_id)
            if (
                active is not None
                and active.status is TurnStatus.QUEUED
                and active.execution_owner_id is not None
            ):
                return active
            return None

    def _persisted_input(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str,
    ) -> TurnInputReceipt | None:
        with self.database.read() as connection:
            if ThreadRepository(connection).get(thread_id) is None:
                raise ThreadNotFoundError(f"thread not found: {thread_id}")
            item = ItemRepository(connection).find_user_message_by_message_id(
                thread_id,
                message_id,
            )
            if item is None:
                return None
            if (
                item.payload.get("text") != prompt
                or item.payload.get("source") != TurnInputSource.STEER.value
            ):
                raise DuplicateMessageConflictError(
                    "messageId was already used with different content"
                )
            turn = TurnRepository(connection).get(item.turn_id)
            if turn is None:
                raise DuplicateMessageConflictError(
                    "idempotent input references a missing Turn"
                )
        return TurnInputReceipt(
            message_id=message_id,
            delivery=TurnInputDelivery.CURRENT_TURN.value,
            turn=turn,
            duplicate=True,
        )

    def _persist_live_input(
        self,
        turn: Turn,
        *,
        prompt: str,
        message_id: str,
        client_surface: ClientSurface,
    ) -> None:
        now = utc_now()
        events: list[DomainEvent] = []
        with self.database.transaction() as connection:
            turns = TurnRepository(connection)
            current = turns.get(turn.id)
            executing = turns.executing_for_thread(turn.thread_id)
            if current is None or executing is None:
                raise NoActiveTurnError(
                    f"thread has no active Turn: {turn.thread_id}",
                    details={"threadId": turn.thread_id, "actualTurnId": None},
                )
            if executing.id != turn.id:
                raise ExpectedTurnMismatchError(turn.id, executing.id)
            if current.status not in {
                TurnStatus.RUNNING,
                TurnStatus.WAITING_APPROVAL,
            }:
                raise TurnNotSteerableError(
                    f"Turn is not steerable in status {current.status.value}",
                    details={
                        "threadId": turn.thread_id,
                        "expectedTurnId": turn.id,
                        "status": current.status.value,
                    },
                )

            items = ItemRepository(connection)
            item = Item(
                thread_id=turn.thread_id,
                turn_id=turn.id,
                ordinal=items.next_ordinal(turn.id),
                kind=ItemKind.USER_MESSAGE,
                status=ItemStatus.COMPLETED,
                summary=prompt[:160],
                payload={
                    "text": prompt,
                    "messageId": message_id,
                    "client": client_surface.value,
                    "delivery": TurnInputDelivery.CURRENT_TURN.value,
                    "source": TurnInputSource.STEER.value,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(item)
            event_repo = EventRepository(connection)
            events.extend(
                (
                    event_repo.append(
                        thread_id=turn.thread_id,
                        turn_id=turn.id,
                        item_id=item.id,
                        type="item.created",
                        payload={"item": item_view(item)},
                    ),
                    event_repo.append(
                        thread_id=turn.thread_id,
                        turn_id=turn.id,
                        item_id=item.id,
                        type="turn.steered",
                        payload={
                            "turnId": turn.id,
                            "messageId": message_id,
                            "delivery": TurnInputDelivery.CURRENT_TURN.value,
                        },
                    ),
                )
            )
        self._publish(events)

        stored = self.session_store.append_message(
            turn.thread_id,
            "user",
            prompt,
            metadata={
                "schemaVersion": 3,
                "client": client_surface.value,
                "turnId": turn.id,
                "messageId": message_id,
                "delivery": TurnInputDelivery.CURRENT_TURN.value,
                "source": TurnInputSource.STEER.value,
            },
        )
        if stored is None:
            raise ThreadNotFoundError(
                f"canonical session disappeared: {turn.thread_id}"
            )
        self.session_runtimes.mark_persisted(turn.thread_id)


__all__ = ["TurnInputReceipt", "TurnInputService"]
