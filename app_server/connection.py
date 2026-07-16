"""Per-client protocol lifecycle and live-event subscription."""

from __future__ import annotations

from dataclasses import dataclass

from core.application.event_service import EventBroker


@dataclass(slots=True)
class ConnectionState:
    broker: EventBroker
    initialized: bool = False
    shutting_down: bool = False
    client_name: str | None = None
    subscription_token: str | None = None

    def initialize(self, client_name: str) -> None:
        if self.subscription_token is None:
            self.subscription_token = self.broker.subscribe()
        self.client_name = client_name
        self.initialized = True

    def close(self) -> None:
        if self.subscription_token is not None:
            self.broker.unsubscribe(self.subscription_token)
            self.subscription_token = None
