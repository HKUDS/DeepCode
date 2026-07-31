"""Narrow ordinary-Turn boundary consumed by the Goal extension."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ContextManager, Protocol

from core.domain.turn import Turn
from core.domain.message_provenance import ClientSurface, TurnInputSource

if TYPE_CHECKING:
    from core.application.turn_service import TurnSnapshot
    from core.events import Event


@dataclass(frozen=True, slots=True)
class GoalTurnAssociation:
    goal_id: str
    skill_ids: tuple[str, ...] = ()
    turn_settlement_ids: frozenset[str] = frozenset()


GoalContextProvider = Callable[[str], GoalTurnAssociation | None]
GoalSubmissionScope = Callable[
    [str],
    ContextManager[GoalTurnAssociation | None],
]


class GoalTurnPort(Protocol):
    def start(
        self,
        thread_id: str,
        *,
        prompt: str,
        message_id: str | None = None,
        skill_ids: tuple[str, ...] = (),
        connection_id: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        event_observer: Callable[["Event"], None] | None = None,
        client_surface: ClientSurface = ClientSurface.INTERNAL,
        input_source: TurnInputSource = TurnInputSource.START,
        expected_goal_id: str | None = None,
    ) -> "TurnSnapshot": ...

    def read(self, turn_id: str) -> "TurnSnapshot": ...

    def active_for_thread(self, thread_id: str) -> Turn | None: ...

    def executing_for_thread(self, thread_id: str) -> Turn | None: ...

    def list_for_goal(self, thread_id: str, goal_id: str) -> tuple[Turn, ...]: ...

    def inject_goal_update(
        self,
        turn_id: str,
        *,
        message_id: str,
        goal_id: str,
        objective: str,
    ) -> bool: ...


__all__ = [
    "GoalContextProvider",
    "GoalSubmissionScope",
    "GoalTurnAssociation",
    "GoalTurnPort",
]
