"""Strict input commands for one executing Turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import logging

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
    InputDeliveryPendingError,
    InputDeliveryUncertainError,
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
    InputDeliveryState,
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
logger = logging.getLogger(__name__)


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
            expected_turn_id=clean_expected,
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
            duplicate = self._persisted_input(
                thread_id,
                prompt=clean_prompt,
                message_id=clean_message_id,
                expected_turn_id=clean_expected,
            )
            if duplicate is not None:
                return duplicate
            raise InputDeliveryUncertainError(
                "mailbox input has no durable delivery confirmation",
                details={
                    "threadId": thread_id,
                    "expectedTurnId": clean_expected,
                    "messageId": clean_message_id,
                },
            )

        item = None
        try:
            item, events = self._record_input_intent(
                executing,
                prompt=clean_prompt,
                message_id=clean_message_id,
                client_surface=client_surface,
            )
            self._publish(events)
            self._append_canonical_input(item)
            self.session_runtimes.commit_input(thread_id, reservation)
            self._set_delivery_state(item.id, InputDeliveryState.ACCEPTED)
        except BaseException as exc:
            self.session_runtimes.cancel_input(thread_id, reservation)
            if item is not None:
                try:
                    self._set_delivery_state(item.id, InputDeliveryState.UNKNOWN)
                except Exception:
                    logger.exception("Could not settle Steer receipt %s", item.id)
                if isinstance(exc, Exception):
                    raise InputDeliveryUncertainError(
                        "Steer delivery could not be confirmed; query its original receipt",
                        details={
                            "threadId": thread_id,
                            "expectedTurnId": clean_expected,
                            "messageId": clean_message_id,
                            "itemId": item.id,
                        },
                    ) from exc
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
        expected_turn_id: str,
    ) -> TurnInputReceipt | None:
        with self.database.read() as connection:
            item = self._find_input(connection, thread_id, message_id)
            if item is None:
                return None
            if (
                item.payload.get("text") != prompt
                or item.payload.get("source") != TurnInputSource.STEER.value
                or item.payload.get("expectedTurnId", item.turn_id) != expected_turn_id
            ):
                raise DuplicateMessageConflictError(
                    "messageId was already used with different content"
                )
            turn = TurnRepository(connection).get(item.turn_id)
            if turn is None:
                raise DuplicateMessageConflictError(
                    "idempotent input references a missing Turn"
                )
            state = self._delivery_state(item, turn)
            if state is not InputDeliveryState.ACCEPTED:
                error = (
                    InputDeliveryPendingError
                    if state is InputDeliveryState.PENDING
                    else InputDeliveryUncertainError
                )
                raise error(
                    "Steer delivery is pending confirmation"
                    if state is InputDeliveryState.PENDING
                    else "Steer delivery is uncertain; this input will not be submitted again",
                    details={
                        "threadId": thread_id,
                        "expectedTurnId": expected_turn_id,
                        "messageId": message_id,
                        "itemId": item.id,
                        "deliveryState": state.value,
                    },
                )
        return TurnInputReceipt(
            message_id=message_id,
            delivery=TurnInputDelivery.CURRENT_TURN.value,
            turn=turn,
            duplicate=True,
        )

    def read(self, thread_id: str, message_id: str) -> Item | None:
        """Locate an admitted input after a lost response without submitting work."""
        message_id = message_id.strip()
        if not message_id:
            raise EmptyInputError("messageId must not be empty")
        with self.database.read() as connection:
            item = self._find_input(connection, thread_id, message_id)
            if (
                item is None
                or item.payload.get("source") != TurnInputSource.STEER.value
            ):
                return item
            turn = TurnRepository(connection).get(item.turn_id)
            return replace(
                item,
                payload={
                    **item.payload,
                    "deliveryState": self._delivery_state(item, turn).value,
                },
            )

    @staticmethod
    def _delivery_state(item: Item, turn: Turn | None) -> InputDeliveryState:
        state = item.payload.get("deliveryState")
        if state == InputDeliveryState.ACCEPTED.value:
            return InputDeliveryState.ACCEPTED
        if (
            state == InputDeliveryState.PENDING.value
            and turn is not None
            and not turn.status.is_terminal
        ):
            return InputDeliveryState.PENDING
        return InputDeliveryState.UNKNOWN

    @staticmethod
    def _find_input(connection, thread_id: str, message_id: str) -> Item | None:
        if ThreadRepository(connection).get(thread_id) is None:
            raise ThreadNotFoundError(f"thread not found: {thread_id}")
        return ItemRepository(connection).find_user_message_by_message_id(
            thread_id, message_id
        )

    def _record_input_intent(
        self,
        turn: Turn,
        *,
        prompt: str,
        message_id: str,
        client_surface: ClientSurface,
    ) -> tuple[Item, list[DomainEvent]]:
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
            if (
                items.find_user_message_by_message_id(turn.thread_id, message_id)
                is not None
            ):
                raise DuplicateMessageConflictError("messageId was already recorded")
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
                    "expectedTurnId": turn.id,
                    "deliveryState": InputDeliveryState.PENDING.value,
                },
                created_at=now,
                updated_at=now,
            )
            items.add(item)
            event_repo = EventRepository(connection)
            events.append(
                event_repo.append(
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    item_id=item.id,
                    type="item.created",
                    payload={"item": item_view(item)},
                )
            )
        return item, events

    def _append_canonical_input(self, item: Item) -> None:
        stored = self.session_store.append_message(
            item.thread_id,
            "user",
            item.payload["text"],
            metadata={
                "schemaVersion": 3,
                "client": item.payload["client"],
                "turnId": item.turn_id,
                "messageId": item.payload["messageId"],
                "delivery": TurnInputDelivery.CURRENT_TURN.value,
                "source": TurnInputSource.STEER.value,
                "expectedTurnId": item.turn_id,
                # A transcript proves persistence, not mailbox acceptance. A
                # rebuilt projection must not fabricate delivery confirmation.
                "deliveryState": InputDeliveryState.UNKNOWN.value,
            },
        )
        if stored is None:
            raise ThreadNotFoundError(
                f"canonical session disappeared: {item.thread_id}"
            )
        self.session_runtimes.mark_persisted(item.thread_id)

    def _set_delivery_state(self, item_id: str, state: InputDeliveryState) -> None:
        with self.database.transaction() as connection:
            items = ItemRepository(connection)
            item = items.get(item_id)
            if item is None:
                raise InputDeliveryUncertainError("Steer receipt disappeared")
            if item.payload.get("deliveryState") == InputDeliveryState.ACCEPTED.value:
                return
            events = self._update_delivery_state(connection, item, state)
            if state is InputDeliveryState.ACCEPTED:
                events.append(
                    EventRepository(connection).append(
                        thread_id=item.thread_id,
                        turn_id=item.turn_id,
                        item_id=item.id,
                        type="turn.steered",
                        payload={
                            "turnId": item.turn_id,
                            "messageId": item.payload["messageId"],
                            "delivery": TurnInputDelivery.CURRENT_TURN.value,
                            "deliveryState": state.value,
                        },
                    )
                )
        self._publish(events)

    @staticmethod
    def _update_delivery_state(
        connection, item: Item, state: InputDeliveryState
    ) -> list[DomainEvent]:
        if item.payload.get("deliveryState") == state.value:
            return []
        updated = replace(
            item,
            payload={**item.payload, "deliveryState": state.value},
            updated_at=utc_now(),
        )
        ItemRepository(connection).update(updated)
        return [
            EventRepository(connection).append(
                thread_id=item.thread_id,
                turn_id=item.turn_id,
                item_id=item.id,
                type="item.updated",
                payload={"item": item_view(updated)},
            )
        ]

    def settle_pending(self, connection, turn_id: str) -> list[DomainEvent]:
        """A terminating Turn cannot leave an unconfirmed input pending forever."""
        events = []
        for item in ItemRepository(connection).list_for_turn(turn_id):
            if (
                item.kind is ItemKind.USER_MESSAGE
                and item.payload.get("source") == TurnInputSource.STEER.value
                and item.payload.get("deliveryState")
                == InputDeliveryState.PENDING.value
            ):
                events.extend(
                    self._update_delivery_state(
                        connection, item, InputDeliveryState.UNKNOWN
                    )
                )
        return events


__all__ = ["TurnInputReceipt", "TurnInputService"]
