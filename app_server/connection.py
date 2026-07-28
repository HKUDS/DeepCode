"""Per-client protocol lifecycle and live-event subscription."""

from __future__ import annotations

from dataclasses import dataclass

from core.application.event_service import EventBroker
from core.domain.message_provenance import ClientSurface


@dataclass(slots=True)
class ConnectionState:
    broker: EventBroker
    initialized: bool = False
    shutting_down: bool = False
    client_name: str | None = None
    client_surface: ClientSurface = ClientSurface.APP_SERVER
    subscription_token: str | None = None

    def initialize(
        self,
        client_name: str,
        client_surface: ClientSurface = ClientSurface.APP_SERVER,
    ) -> None:
        if self.subscription_token is None:
            self.subscription_token = self.broker.subscribe()
        self.client_name = client_name
        self.client_surface = client_surface
        self.initialized = True

    def close(self) -> None:
        if self.subscription_token is not None:
            self.broker.unsubscribe(self.subscription_token)
            self.subscription_token = None
