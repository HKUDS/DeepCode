"""Narrow Turn boundary consumed by Goal orchestration."""

from __future__ import annotations

from typing import Callable, Protocol

from core.application.turn_service import TurnSnapshot
from core.domain.turn import Turn
from core.events import Event


class GoalTurnPort(Protocol):
    def start_goal_attempt(
        self,
        thread_id: str,
        *,
        prompt: str,
        skill_ids: tuple[str, ...],
        goal_id: str,
        goal_definition_revision: int,
        goal_attempt_id: str,
        event_observer: Callable[[Event], None] | None = None,
    ) -> TurnSnapshot: ...

    def read(self, turn_id: str) -> TurnSnapshot: ...

    def active_for_thread(self, thread_id: str) -> Turn | None: ...

    def interrupt(self, turn_id: str) -> tuple[bool, Turn]: ...


__all__ = ["GoalTurnPort"]
