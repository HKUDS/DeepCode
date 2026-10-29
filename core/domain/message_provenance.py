"""Typed provenance for canonical user input and its resulting Turn."""

from __future__ import annotations

from collections.abc import Mapping
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


class GoalPrompt(StrEnum):
    """Who asked for a Turn whose prompt the Goal runtime wrote."""

    AUTOMATIC = "automatic"  # the runtime continued the Goal on its own
    REQUESTED = "requested"  # a person started, continued or retried it


def goal_prompt(provenance: Mapping[str, object]) -> GoalPrompt | None:
    """Whether a user-role record is the Goal runtime's instruction.

    ``provenance`` is a user message item's payload or its JSONL record's
    metadata; both carry ``source`` and ``client``, and a retry carries the
    first retried Turn's under ``retryOf``. The prompt such a record holds
    was written for the model, not typed by the user, so clients must not
    show it as the user's words.
    """
    source = provenance.get("source")
    if source == TurnInputSource.RETRY.value:
        retried = provenance.get("retryOf")
        if (
            isinstance(retried, Mapping)
            and retried.get("source") == TurnInputSource.GOAL_CONTINUATION.value
        ):
            return GoalPrompt.REQUESTED
        return None
    if source != TurnInputSource.GOAL_CONTINUATION.value:
        return None
    if provenance.get("client") == ClientSurface.INTERNAL.value:
        return GoalPrompt.AUTOMATIC
    return GoalPrompt.REQUESTED


__all__ = [
    "ClientSurface",
    "GoalPrompt",
    "TurnInputDelivery",
    "InputDeliveryState",
    "TurnInputSource",
    "goal_prompt",
]
