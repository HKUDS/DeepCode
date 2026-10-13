"""Instance-local browser sessions; all access belongs to the HTTP event loop.

A session is never a cookie: browsers send a host's cookies to all of its
ports, so every other local server the user visits would receive it. The page
keeps the session in its own origin's storage and presents it explicitly.
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable

from aiohttp import web

# Browsers cannot set headers on a WebSocket handshake, so the page offers its
# session as a second subprotocol beside the one the service selects.
RPC_PROTOCOL = "deepcode.rpc"
SESSION_PROTOCOL = "deepcode.session."


def _presented(request: web.Request) -> str:
    scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
    if scheme == "Bearer":
        return credential
    offered = [
        protocol.strip()
        for protocol in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
    ]
    # A handshake that cannot select the RPC protocol logs every offered one.
    if RPC_PROTOCOL in offered:
        for protocol in offered:
            if protocol.startswith(SESSION_PROTOCOL):
                return protocol.removeprefix(SESSION_PROTOCOL)
    return ""


class BrowserAuth:
    """Sessions live in memory, so a service restart invalidates all of them."""

    TICKET_TTL = 60
    SESSION_TTL = 12 * 60 * 60
    CAPACITY = 64
    EXCHANGES_PER_MINUTE = 60

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
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
        session = _presented(request)
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
