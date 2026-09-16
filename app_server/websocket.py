"""Bounded aiohttp transport over the same RPC peer used by stdio."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import threading

from aiohttp import WSMsgType, web

from app_server.browser_auth import BrowserAuth
from app_server.errors import RpcError
from app_server.host import ServiceHost
from app_server.peer import RpcPeer
from app_server.protocol.models import Request


async def _close_socket(
    ws: web.WebSocketResponse, *, code: int = 1000, message: bytes = b""
) -> None:
    # aiohttp's close timeout covers reading the peer's reply, not all writes.
    # Bound the whole close so a stalled client cannot hold logout or shutdown.
    try:
        await asyncio.wait_for(ws.close(code=code, message=message, drain=False), 3)
    except TimeoutError:
        pass


class FrameQueue:
    """A bounded frame queue, including its scheduled wakeups.

    Peer writers never wait for a socket. Overflow disconnects that client so
    replies cannot be silently dropped; durable events remain replayable.
    """

    def __init__(self, *, max_frames: int = 1024, max_bytes: int = 8 * 1024 * 1024):
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._lock = threading.Lock()
        self._frames: deque[bytes] = deque()
        self._size = 0
        self._closed = False
        self._finished = False
        self._max_frames = max_frames
        self._max_bytes = max_bytes
        self.overflowed = False

    def send(self, frame: bytes) -> None:
        with self._lock:
            if self._closed or self._finished:
                raise BrokenPipeError("WebSocket disconnected")
            if (
                len(self._frames) >= self._max_frames
                or self._size + len(frame) > self._max_bytes
            ):
                self.overflowed = True
                self._close_locked()
                raise BrokenPipeError("WebSocket frame capacity exceeded")
            wake = not self._frames
            self._frames.append(frame)
            self._size += len(frame)
            if wake:
                self._loop.call_soon_threadsafe(self._wake.set)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def finish(self) -> None:
        """Close after the accepted frames have been written."""
        with self._lock:
            self._finished = True
            self._loop.call_soon_threadsafe(self._wake.set)

    def _close_locked(self) -> None:
        if not self._closed:
            self._closed = True
            self._frames.clear()
            self._size = 0
            self._loop.call_soon_threadsafe(self._wake.set)

    async def receive(self) -> bytes | None:
        while True:
            await self._wake.wait()
            with self._lock:
                if self._closed:
                    return None
                if self._frames:
                    frame = self._frames.popleft()
                    self._size -= len(frame)
                    if not self._frames and not self._finished:
                        self._wake.clear()
                    return frame
                if self._finished:
                    return None
                self._wake.clear()


class WebSocketTransport:
    MAX_CONNECTIONS = 32
    # Business calls have their own bounded pool; management stays responsive.
    MAX_REQUESTS = 8
    DRAIN_METHODS = frozenset(
        {
            "initialize",
            "shutdown",
            "event/replay",
            "thread/read",
            "turn/read",
            "turn/input/read",
            "approval/respond",
            "turn/interrupt",
            "workflow/respond",
            "workflow/interrupt",
            "terminal/close",
            "terminal/list",
            "terminal/read",
        }
    )

    def __init__(
        self,
        host: ServiceHost,
        auth: BrowserAuth,
        *,
        native_authenticated: Callable[[web.Request], bool],
        phase: Callable[[], str],
        service_info: dict,
    ) -> None:
        self.host = host
        self.auth = auth
        self._native_authenticated = native_authenticated
        self._phase = phase
        self._service_info = service_info
        self._connections: dict[web.WebSocketResponse, str | None] = {}
        self._slots = asyncio.Semaphore(self.MAX_REQUESTS)
        self._executor = ThreadPoolExecutor(
            max_workers=self.MAX_REQUESTS, thread_name_prefix="deepcode-rpc"
        )
        self._inflight: set[asyncio.Future] = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._closing = False

    async def wait_idle(self, timeout: float) -> bool:
        if self._idle.is_set():
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def _guard(self, request: Request) -> None:
        phase = self._phase()
        if (
            self._closing
            or phase == "stopping"
            or (phase != "ready" and request.method not in self.DRAIN_METHODS)
        ):
            raise RpcError(
                -32000,
                "Service is draining; request was not dispatched",
                stable_code="SERVICE_DRAINING",
                data={"retryable": True},
            )

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if "Origin" in request.headers:
            session = self.auth.require(request)
        elif self._native_authenticated(request):
            session = None
        else:
            raise web.HTTPUnauthorized(text="Business RPC requires authentication")
        if self._closing or self._phase() != "ready":
            raise web.HTTPServiceUnavailable(
                text="Service is not accepting connections"
            )
        if len(self._connections) >= self.MAX_CONNECTIONS:
            raise web.HTTPServiceUnavailable(text="Too many RPC connections")
        ws = web.WebSocketResponse(
            heartbeat=30,
            timeout=2,
            max_msg_size=self.host.max_message_bytes,
            compress=False,
        )
        # Reserve before the first await, including handshakes in progress.
        self._connections[ws] = session
        outbox = FrameQueue()
        incoming = FrameQueue(max_frames=32, max_bytes=4 * 1024 * 1024)
        peer = None
        writer = expiry = dispatcher = None
        try:
            await ws.prepare(request)
            if self._closing or (
                session is not None and not self.auth.remaining(session)
            ):
                await _close_socket(
                    ws, code=1008, message=b"Connection authorization ended"
                )
                return ws
            # The host is already started; registration only takes short locks
            # and starts the peer pump. Keep ownership transfer uncancellable.
            peer = self.host.connect(outbox.send, service_info=self._service_info)
            writer = asyncio.create_task(self._write(ws, outbox))
            dispatcher = asyncio.create_task(
                self._dispatch(ws, peer, incoming, outbox, session)
            )
            if session is not None:
                expiry = asyncio.create_task(self._expire(ws, session))
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    if message.type == WSMsgType.BINARY:
                        await _close_socket(
                            ws, code=1003, message=b"JSON text frames required"
                        )
                    break
                try:
                    incoming.send(message.data.encode("utf-8"))
                except BrokenPipeError:
                    await _close_socket(
                        ws, code=1013, message=b"Request capacity exceeded"
                    )
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            incoming.close()
            outbox.close()
            if dispatcher is not None:
                await asyncio.gather(dispatcher, return_exceptions=True)
            if peer is not None:
                await asyncio.to_thread(peer.close)
            for task in (writer, expiry):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (writer, expiry) if t is not None), return_exceptions=True
            )
            if ws.prepared:
                await _close_socket(ws)
            self._connections.pop(ws, None)
        return ws

    async def _dispatch(
        self,
        ws: web.WebSocketResponse,
        peer: RpcPeer,
        incoming: FrameQueue,
        outgoing: FrameQueue,
        session: str | None,
    ) -> None:
        """Keep request order without blocking the socket's ping/pong reader."""
        work = None
        try:
            while (raw := await incoming.receive()) is not None:
                async with self._slots:
                    if ws.closed or self._closing:
                        break
                    if session is not None and not self.auth.remaining(session):
                        await _close_socket(
                            ws, code=1008, message=b"Browser session expired"
                        )
                        break
                    work = asyncio.get_running_loop().run_in_executor(
                        self._executor,
                        partial(peer.receive, raw, before_dispatch=self._guard),
                    )
                    self._inflight.add(work)
                    self._idle.clear()
                    work.add_done_callback(self._finished)
                    # Disconnect/cancellation must not cancel an admitted mutation.
                    await asyncio.shield(work)
                if peer.closed:
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            # A shutdown reply precedes the WebSocket close frame.
            outgoing.finish()

    def _finished(self, task: asyncio.Future) -> None:
        self._inflight.discard(task)
        if not self._inflight:
            self._idle.set()

    async def _write(self, ws: web.WebSocketResponse, outbox: FrameQueue) -> None:
        try:
            while (frame := await outbox.receive()) is not None:
                await asyncio.wait_for(
                    ws.send_str(frame.decode("utf-8").rstrip("\n")), 10
                )
        except (TimeoutError, ConnectionError, OSError):
            outbox.close()
        finally:
            await _close_socket(ws, code=1013 if outbox.overflowed else 1000)

    async def _expire(self, ws: web.WebSocketResponse, session: str) -> None:
        await asyncio.sleep(self.auth.remaining(session))
        await _close_socket(ws, code=1008, message=b"Browser session expired")

    async def revoke(self, session: str) -> None:
        self.auth.revoke(session)
        await asyncio.gather(
            *(
                _close_socket(ws, code=1008, message=b"Browser session revoked")
                for ws, owner in tuple(self._connections.items())
                if owner == session and ws.prepared
            )
        )

    async def shutdown(self, _app: web.Application) -> None:
        self._closing = True
        await asyncio.gather(
            *(
                _close_socket(ws, code=1001, message=b"Service stopping")
                for ws in tuple(self._connections)
                if ws.prepared
            )
        )

    async def cleanup(self, _app: web.Application) -> None:
        await asyncio.gather(*tuple(self._inflight), return_exceptions=True)
        await asyncio.to_thread(self._executor.shutdown, wait=True)
