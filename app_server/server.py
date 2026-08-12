"""Single-client stdio JSON-RPC server."""

from __future__ import annotations

import logging
import threading
from typing import Any, BinaryIO

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
from app_server.protocol.models import Response, notification
from core.application.application import DeepCodeApplication
from core.application.errors import ApplicationError
from core.application.event_service import DeliveryBatch
from core.application.views import event_view

logger = logging.getLogger(__name__)


class AppServer:
    def __init__(
        self,
        application: DeepCodeApplication,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        self.application = application
        self.max_message_bytes = max_message_bytes

    def serve(self, source: BinaryIO, sink: BinaryIO) -> int:
        connection = ConnectionState(self.application.broker)
        dispatcher = Dispatcher(
            self.application,
            connection,
            max_message_bytes=self.max_message_bytes,
        )
        delivery_lock = threading.RLock()
        stop_pump = threading.Event()

        def deliver_terminal(method: str, payload: dict[str, Any]) -> None:
            try:
                with delivery_lock:
                    self._write_notification(sink, notification(method, payload))
            except (BrokenPipeError, OSError, ValueError):
                stop_pump.set()

        terminal_token = self.application.terminals.subscribe(deliver_terminal)

        def deliver_skill_change(project_id: str) -> None:
            try:
                with delivery_lock:
                    self._write_notification(
                        sink,
                        notification(
                            rpc_notifications.SKILLS_CHANGED,
                            {"projectId": project_id},
                        ),
                    )
            except (BrokenPipeError, OSError, ValueError):
                stop_pump.set()

        skill_token = self.application.skills.subscribe_changes(deliver_skill_change)

        def deliver_plugin_change(_discovery) -> None:
            try:
                with delivery_lock:
                    self._write_notification(
                        sink,
                        notification(rpc_notifications.PLUGINS_CHANGED, {}),
                    )
            except (BrokenPipeError, OSError, ValueError):
                stop_pump.set()

        plugin_token = self.application.plugins.subscribe_changes(deliver_plugin_change)

        def deliver_mcp_change() -> None:
            try:
                with delivery_lock:
                    self._write_notification(
                        sink,
                        notification(rpc_notifications.MCP_CHANGED, {}),
                    )
            except (BrokenPipeError, OSError, ValueError):
                stop_pump.set()

        mcp_token = self.application.mcp.subscribe_changes(deliver_mcp_change)
        pump = threading.Thread(
            target=self._pump_events,
            args=(sink, connection, delivery_lock, stop_pump),
            name="deepcode-event-pump",
            daemon=True,
        )
        pump.start()
        try:
            while not connection.shutting_down:
                request = None
                raw = source.readline(self.max_message_bytes + 1)
                if not raw:
                    break
                if len(raw) > self.max_message_bytes:
                    self._discard_remainder(source, raw)
                    with delivery_lock:
                        self._write_error(
                            sink,
                            None,
                            RpcError(
                                -32600,
                                "message exceeds the configured size limit",
                                stable_code="INVALID_REQUEST",
                            ),
                        )
                    continue
                with delivery_lock:
                    try:
                        request = decode_request(raw, max_bytes=self.max_message_bytes)
                        result = dispatcher.dispatch(request)
                    except ApplicationError as exc:
                        if request is not None and request.has_id:
                            self._write_error(
                                sink, request.id, from_application_error(exc)
                            )
                        continue
                    except RpcError as exc:
                        request_id = (
                            request.id
                            if request is not None and request.has_id
                            else None
                        )
                        if request is None or request.has_id:
                            self._write_error(sink, request_id, exc)
                        continue
                    except Exception:
                        logger.exception("Unhandled App Server request failure")
                        if request is not None and request.has_id:
                            self._write_error(
                                sink,
                                request.id,
                                RpcError(
                                    -32603,
                                    "internal error",
                                    stable_code="INTERNAL_ERROR",
                                ),
                            )
                        continue

                    if request.has_id:
                        self._write_response(
                            sink,
                            Response(id=request.id, result=result).to_dict(),
                            method=request.method,
                        )
                    self._drain_events(sink, connection)
        finally:
            stop_pump.set()
            pump.join(timeout=1.0)
            self.application.terminals.unsubscribe(terminal_token)
            self.application.skills.unsubscribe_changes(skill_token)
            self.application.plugins.unsubscribe_changes(plugin_token)
            self.application.mcp.unsubscribe_changes(mcp_token)
            connection.close()
            self.application.close()
        return 0

    def _pump_events(
        self,
        sink: BinaryIO,
        connection: ConnectionState,
        delivery_lock: threading.RLock,
        stop: threading.Event,
    ) -> None:
        while not stop.is_set():
            token = connection.subscription_token
            if token is None:
                stop.wait(0.05)
                continue
            if not self.application.broker.wait_for_events(token, timeout=0.25):
                continue
            try:
                with delivery_lock:
                    batch = self.application.broker.drain(token)
                    self._write_batch(sink, batch)
            except (BrokenPipeError, OSError, ValueError):
                logger.exception("App Server event delivery failed")
                stop.set()

    def _drain_events(self, sink: BinaryIO, connection: ConnectionState) -> None:
        token = connection.subscription_token
        if token is None:
            return
        self._write_batch(sink, self.application.broker.drain(token))

    def _write_batch(self, sink: BinaryIO, batch: DeliveryBatch) -> None:
        """Write one already-drained live batch while the caller owns the sink lock."""
        if batch.dropped:
            self._write(
                sink,
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
            self._write_notification(sink, notification(method, event_view(event)))

    @staticmethod
    def _discard_remainder(source: BinaryIO, first_chunk: bytes) -> None:
        chunk = first_chunk
        while chunk and not chunk.endswith(b"\n"):
            chunk = source.readline(64 * 1024)

    def _write_error(self, sink: BinaryIO, request_id: Any, error: RpcError) -> None:
        self._write(
            sink,
            Response(id=request_id, error=error.payload()).to_dict(),
        )

    def _write_response(
        self,
        sink: BinaryIO,
        message: dict[str, Any],
        *,
        method: str | None = None,
    ) -> None:
        encoded = encode_message(message)
        if len(encoded) <= self.max_message_bytes:
            self._write_encoded(sink, encoded)
            return
        if method == rpc_methods.EVENT_REPLAY:
            replay_page = self._fit_replay_response(message)
            if replay_page is not None:
                self._write_encoded(sink, replay_page)
                return
        request_id = message.get("id")
        error = RpcError(
            -32004,
            "response exceeds the configured message limit",
            stable_code="RESPONSE_TOO_LARGE",
            data={"maxMessageBytes": self.max_message_bytes},
        )
        self._write(
            sink,
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

    def _write_notification(self, sink: BinaryIO, message: dict[str, Any]) -> None:
        encoded = encode_message(message)
        if len(encoded) <= self.max_message_bytes:
            self._write_encoded(sink, encoded)
            return
        warning = notification(
            rpc_notifications.SERVER_WARNING,
            {
                "code": "NOTIFICATION_TOO_LARGE",
                "dropped": 1,
                "replayRequired": True,
            },
        )
        self._write(sink, warning)

    def _write(self, sink: BinaryIO, message: dict[str, Any]) -> None:
        encoded = encode_message(message)
        if len(encoded) > self.max_message_bytes:
            raise ValueError("outgoing message exceeds the configured size limit")
        self._write_encoded(sink, encoded)

    @staticmethod
    def _write_encoded(sink: BinaryIO, encoded: bytes) -> None:
        sink.write(encoded)
        sink.flush()
