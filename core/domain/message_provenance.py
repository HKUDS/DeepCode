"""Typed provenance for canonical user input and its resulting Turn."""

from __future__ import annotations

from enum import StrEnum


class ClientSurface(StrEnum):
    CLI = "cli"
    DESKTOP = "desktop"
    WEB = "web"
    HEADLESS = "headless"
    AUTOMATION = "automation"
    APP_SERVER = "app_server"
    INTERNAL = "internal"


class TurnInputSource(StrEnum):
    START = "start"
    STEER = "steer"
    QUEUE = "queue"
    GOAL_CONTINUATION = "goal_continuation"
    AUTOMATION = "automation"
    RETRY = "retry"
    TURN_INTERRUPT = "turn_interrupt"


class TurnInputDelivery(StrEnum):
    CURRENT_TURN = "current_turn"
    NEXT_TURN = "next_turn"


class InputDeliveryState(StrEnum):
    """Confirmation that a durable Steer was accepted by its live mailbox."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"


__all__ = [
    "ClientSurface",
    "TurnInputDelivery",
    "InputDeliveryState",
    "TurnInputSource",
]
