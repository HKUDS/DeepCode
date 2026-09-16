"""One RPC client's requests and bounded notifications, independent of its host."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from app_server.connection import ConnectionState
from app_server.dispatcher import Dispatcher
from app_server.errors import RpcError, from_application_error
from app_server.protocol import methods as rpc_methods
from app_server.protocol import notifications as rpc_notifications
from app_server.protocol.codec import (
    DEFAULT_MAX_MESSAGE_BYTES,
    decode_request,
    encode_message,
)
from app_server.protocol.models import Request, Response, notification
from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.application.event_service import DeliveryBatch
from core.application.views import event_view

logger = logging.getLogger(__name__)


class RpcPeer:
    """Serialize one client's output; disconnect never closes the application.

    ``send`` belongs to the transport and must either complete or raise on
    disconnection. Transport adapters own cancellation of blocked socket/pipe I/O.
    Host notifications only enter a bounded queue, never write to a client.
    """

    def __init__(
        self,
        application: DeepCodeApplication,
        send: Callable[[bytes], None],
        *,
        on_close: Callable[[RpcPeer], None],
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        notification_capacity: int = 256,
        service_info: dict[str, Any] | None = None,
    ) -> None:
        if notification_capacity < 1:
            raise ValueError("notification_capacity must be positive")
        self.application = application
        self.max_message_bytes = max_message_bytes
        self.connection = ConnectionState(application.broker)
        self.dispatcher = Dispatcher(
            application,
            self.connection,
            max_message_bytes=max_message_bytes,
            service_info=service_info,
        )
        self._send = send
        self._on_close = on_close
        self._delivery_lock = threading.RLock()
        self._queue_lock = threading.Lock()
        self._pending: deque[dict[str, Any]] = deque(maxlen=notification_capacity)
        self._dropped = 0
        self._stop = threading.Event()
        self._pump = threading.Thread(
            target=self._pump_events, name="deepcode-event-pump", daemon=True
        )

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    def start(self) -> None:
        self._pump.start()

    def close(self) -> None:
        with self._delivery_lock:
            if self.closed:
                return
            self._stop.set()
            with self._queue_lock:
                self._pending.clear()
            self.connection.close()
        self._on_close(self)
        if (
            self._pump.ident is not None
            and threading.current_thread() is not self._pump
        ):
            self._pump.join(timeout=1.0)

    def notify(self, method: str, payload: dict[str, Any]) -> None:
        with self._queue_lock:
            if self.closed or not self.connection.initialized:
                return
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append(notification(method, payload))

    def receive(
        self, raw: bytes, *, before_dispatch: Callable[[Request], None] | None = None
    ) -> None:
        """Dispatch one complete frame; responses precede its queued events."""
        try:
            with self._delivery_lock:
                if self.closed or self.connection.shutting_down:
                    return
                request = None
                try:
                    request = decode_request(raw, max_bytes=self.max_message_bytes)
                    if before_dispatch is not None:
                        before_dispatch(request)
                    result = self.dispatcher.dispatch(request)
                except ApplicationError as exc:
                    if request is not None and request.has_id:
                        self._write_error(request.id, from_application_error(exc))
                except RpcError as exc:
                    if request is None or request.has_id:
                        self._write_error(
                            request.id if request is not None else None, exc
                        )
                except Exception:
                    logger.exception("Unhandled App Server request failure")
                    if request is not None and request.has_id:
                        self._write_error(
                            request.id,
                            RpcError(
                                -32603, "internal error", stable_code="INTERNAL_ERROR"
                            ),
                        )
                else:
                    if request.has_id:
                        self._write_response(
                            Response(id=request.id, result=result).to_dict(),
                            method=request.method,
                        )
                self._drain_events()
        except (BrokenPipeError, OSError, ValueError):
            self.close()
            raise
        if self.connection.shutting_down:
            self.close()

    def reject_oversized_frame(self) -> None:
        try:
            with self._delivery_lock:
                if not self.closed:
                    self._write_error(
                        None,
                        RpcError(
                            -32600,
                            "message exceeds the configured size limit",
                            stable_code="INVALID_REQUEST",
                        ),
                    )
        except (BrokenPipeError, OSError, ValueError):
            self.close()
            raise

    def _pump_events(self) -> None:
        try:
            while not self.closed:
                token = self.connection.subscription_token
                if token is None:
                    self._stop.wait(0.05)
                else:
                    self.application.broker.wait_for_events(token, timeout=0.25)
                with self._delivery_lock:
                    if self.closed:
                        return
                    self._drain_events()
        except (BrokenPipeError, OSError, ValueError):
            logger.debug("App Server client disconnected during event delivery")
            self.close()

    def _drain_events(self) -> None:
        with self._queue_lock:
            pending = tuple(self._pending)
            dropped = self._dropped
            self._pending.clear()
            self._dropped = 0
        if dropped:
            self._write_notification(
                notification(
                    rpc_notifications.SERVER_WARNING,
                    {
                        "code": "NOTIFICATION_QUEUE_OVERFLOW",
                        "dropped": dropped,
                        "replayRequired": True,
                    },
                )
            )
        for message in pending:
            self._write_notification(message)
        token = self.connection.subscription_token
        if token is not None:
            self._write_batch(self.application.broker.drain(token))

    def _write_batch(self, batch: DeliveryBatch) -> None:
        """Write one already-drained live batch while the caller owns the sink lock."""
        if batch.dropped:
            self._write(
                notification(
                    rpc_notifications.SERVER_WARNING,
                    {
                        "code": "EVENT_QUEUE_OVERFLOW",
                        "dropped": batch.dropped,
                        "replayRequired": True,
                    },
                ),
            )
        for event in batch.events:
            method = (
                rpc_notifications.THREAD_UPDATED
                if event.type.startswith("thread.")
                else event.type
            )
            self._write_notification(notification(method, event_view(event)))

    def _write_error(self, request_id: Any, error: RpcError) -> None:
        self._write(
            Response(id=request_id, error=error.payload()).to_dict(),
        )

    def _write_response(
        self,
        message: dict[str, Any],
        *,
        method: str | None = None,
    ) -> None:
        encoded = encode_message(message)
        if len(encoded) <= self.max_message_bytes:
            self._send(encoded)
            return
        if method == rpc_methods.EVENT_REPLAY:
            replay_page = self._fit_replay_response(message)
            if replay_page is not None:
                self._send(replay_page)
                return
        if method == rpc_methods.INITIALIZE:
            # A rejected handshake must not leave a subscribed, initialized client.
            self.connection.close()
        request_id = message.get("id")
        error = RpcError(
            -32004,
            "response exceeds the configured message limit",
            stable_code="RESPONSE_TOO_LARGE",
            data={"maxMessageBytes": self.max_message_bytes},
        )
        self._write(
            Response(id=request_id, error=error.payload()).to_dict(),
        )

    def _fit_replay_response(self, message: dict[str, Any]) -> bytes | None:
        """Return the largest replay prefix that fits one transport message."""

        result = message.get("result")
        if not isinstance(result, dict):
            return None
        events = result.get("events")
        if not isinstance(events, list) or not events:
            return None

        best: bytes | None = None
        low = 1
        high = len(events)
        while low <= high:
            count = (low + high) // 2
            last_event = events[count - 1]
            if not isinstance(last_event, dict):
                return None
            sequence = last_event.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                return None
            candidate = {
                **message,
                "result": {
                    **result,
                    "events": events[:count],
                    "nextAfter": sequence,
                    "hasMore": True,
                },
            }
            encoded = encode_message(candidate)
            if len(encoded) <= self.max_message_bytes:
                best = encoded
                low = count + 1
            else:
                high = count - 1
        return best

    def _write_notification(self, message: dict[str, Any]) -> None:
        encoded = encode_message(message)
        if len(encoded) <= self.max_message_bytes:
            self._send(encoded)
            return
        warning = notification(
            rpc_notifications.SERVER_WARNING,
            {
                "code": "NOTIFICATION_TOO_LARGE",
                "dropped": 1,
                "replayRequired": True,
            },
        )
        self._write(warning)

    def _write(self, message: dict[str, Any]) -> None:
        encoded = encode_message(message)
        if len(encoded) > self.max_message_bytes:
            raise ValueError("outgoing message exceeds the configured size limit")
        self._send(encoded)
