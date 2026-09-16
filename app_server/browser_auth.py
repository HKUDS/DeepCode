"""Instance-local browser sessions; all access belongs to the HTTP event loop."""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable

from aiohttp import web


class BrowserAuth:
    TICKET_TTL = 60
    SESSION_TTL = 12 * 60 * 60
    CAPACITY = 64
    EXCHANGES_PER_MINUTE = 60

    def __init__(
        self, instance_id: str, *, clock: Callable[[], float] = time.monotonic
    ):
        # Cookies are not scoped by port. Instance-specific names avoid collisions
        # between separate local databases, and restart invalidates all sessions.
        self.cookie_name = f"deepcode_session_{instance_id}"
        self._clock = clock
        self._tickets: dict[str, float] = {}
        self._sessions: dict[str, float] = {}
        self._attempts: deque[float] = deque()

    def issue(self) -> dict[str, str | int]:
        self._prune(self._tickets)
        if len(self._tickets) >= self.CAPACITY:
            raise web.HTTPTooManyRequests(text="Too many pending browser links")
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = self._clock() + self.TICKET_TTL
        return {"ticket": ticket, "expiresIn": self.TICKET_TTL}

    def exchange(self, ticket: object) -> str:
        now = self._clock()
        while self._attempts and self._attempts[0] <= now - 60:
            self._attempts.popleft()
        if len(self._attempts) >= self.EXCHANGES_PER_MINUTE:
            raise web.HTTPTooManyRequests(text="Too many exchange attempts")
        self._attempts.append(now)
        self._prune(self._tickets)
        self._prune(self._sessions)
        if not isinstance(ticket, str) or ticket not in self._tickets:
            raise web.HTTPUnauthorized(text="Invalid or expired browser link")
        if len(self._sessions) >= self.CAPACITY:
            raise web.HTTPTooManyRequests(text="Too many browser sessions")
        del self._tickets[ticket]
        session = secrets.token_urlsafe(32)
        self._sessions[session] = now + self.SESSION_TTL
        return session

    def remaining(self, session: str) -> float:
        return max(0.0, self._sessions.get(session, 0.0) - self._clock())

    def require(self, request: web.Request) -> str:
        session = request.cookies.get(self.cookie_name, "")
        if not self.remaining(session):
            raise web.HTTPUnauthorized(text="Browser session expired; open a new link")
        return session

    def revoke(self, session: str) -> None:
        self._sessions.pop(session, None)

    def _prune(self, entries: dict[str, float]) -> None:
        now = self._clock()
        for key, expiry in tuple(entries.items()):
            if expiry <= now:
                del entries[key]
