"""Frontend-neutral routing for one interactive composer submission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from core.application.errors import (
    ExpectedTurnMismatchError,
    NoActiveTurnError,
    TurnAlreadyRunningError,
    TurnNotSteerableError,
)
from core.application.turn_service import TurnService
from core.domain.message_provenance import ClientSurface
from core.domain.turn import Turn
from core.events import Event


class InteractiveDelivery(StrEnum):
    STARTED = "started"
    STEERED = "steered"


@dataclass(frozen=True, slots=True)
class InteractiveTurnResult:
    delivery: InteractiveDelivery
    turn: Turn
    message_id: str
    duplicate: bool = False


class InteractiveTurnRouter:
    """Apply the Codex start/steer race contract without parsing user text.

    One submission performs its initially selected operation and, for the two
    known active/idle races, at most one corrective operation. The same client
    message ID is reused throughout so a transport retry cannot duplicate work.
    """

    def __init__(
        self,
        turns: TurnService,
        *,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
    ) -> None:
        self.turns = turns
        self.client_surface = client_surface

    def send(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str,
        cached_active_turn_id: str | None,
        skill_ids: tuple[str, ...] = (),
        event_observer: Callable[[Event], None] | None = None,
    ) -> InteractiveTurnResult:
        if cached_active_turn_id:
            return self._send_with_cached_active(
                thread_id,
                expected_turn_id=cached_active_turn_id,
                prompt=prompt,
                message_id=message_id,
                skill_ids=skill_ids,
                event_observer=event_observer,
            )
        return self._start_or_steer_once(
            thread_id,
            prompt=prompt,
            message_id=message_id,
            skill_ids=skill_ids,
            event_observer=event_observer,
        )

    def _send_with_cached_active(
        self,
        thread_id: str,
        *,
        expected_turn_id: str,
        prompt: str,
        message_id: str,
        skill_ids: tuple[str, ...],
        event_observer: Callable[[Event], None] | None,
    ) -> InteractiveTurnResult:
        try:
            return self._steer_once(
                thread_id,
                expected_turn_id=expected_turn_id,
                prompt=prompt,
                message_id=message_id,
            )
        except NoActiveTurnError:
            return self._start_or_steer_once(
                thread_id,
                prompt=prompt,
                message_id=message_id,
                skill_ids=skill_ids,
                event_observer=event_observer,
            )
        except ExpectedTurnMismatchError as exc:
            actual_turn_id = _actual_turn_id(exc)
            if actual_turn_id is None:
                raise
            return self._steer_once(
                thread_id,
                expected_turn_id=actual_turn_id,
                prompt=prompt,
                message_id=message_id,
            )
        except TurnNotSteerableError as exc:
            if not exc.crossed_final_input_boundary:
                raise
            terminal = self.turns.wait_until_terminal(expected_turn_id)
            if terminal is None:
                raise
            return self._start_or_steer_once(
                thread_id,
                prompt=prompt,
                message_id=message_id,
                skill_ids=skill_ids,
                event_observer=event_observer,
            )

    def _start_or_steer_once(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str,
        skill_ids: tuple[str, ...],
        event_observer: Callable[[Event], None] | None,
    ) -> InteractiveTurnResult:
        try:
            return self._start(
                thread_id,
                prompt=prompt,
                message_id=message_id,
                skill_ids=skill_ids,
                event_observer=event_observer,
            )
        except TurnAlreadyRunningError as exc:
            actual_turn_id = _actual_turn_id(exc)
            if actual_turn_id is None:
                raise
            return self._steer_once(
                thread_id,
                expected_turn_id=actual_turn_id,
                prompt=prompt,
                message_id=message_id,
            )

    def _start(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str,
        skill_ids: tuple[str, ...],
        event_observer: Callable[[Event], None] | None,
    ) -> InteractiveTurnResult:
        snapshot = self.turns.start(
            thread_id,
            prompt=prompt,
            message_id=message_id,
            client_surface=self.client_surface,
            skill_ids=skill_ids,
            event_observer=event_observer,
        )
        return InteractiveTurnResult(
            delivery=InteractiveDelivery.STARTED,
            turn=snapshot.turn,
            message_id=message_id,
        )

    def _steer_once(
        self,
        thread_id: str,
        *,
        expected_turn_id: str,
        prompt: str,
        message_id: str,
    ) -> InteractiveTurnResult:
        receipt = self.turns.steer(
            thread_id,
            expected_turn_id=expected_turn_id,
            prompt=prompt,
            message_id=message_id,
            client_surface=self.client_surface,
        )
        return InteractiveTurnResult(
            delivery=InteractiveDelivery.STEERED,
            turn=receipt.turn,
            message_id=message_id,
            duplicate=receipt.duplicate,
        )


def _actual_turn_id(
    error: TurnAlreadyRunningError | ExpectedTurnMismatchError,
) -> str | None:
    value = error.details.get("actualTurnId")
    return str(value) if isinstance(value, str) and value else None


__all__ = [
    "InteractiveDelivery",
    "InteractiveTurnResult",
    "InteractiveTurnRouter",
]
