"""Application ownership and shared notifications for RPC transports."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import ExitStack
from typing import Any

from app_server.config_watch import ConfigFileWatcher
from app_server.peer import RpcPeer
from app_server.protocol import notifications
from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES
from core.application.application import DeepCodeApplication


class ServiceHost:
    """Own one application until explicitly closed, even with no clients.

    Transports own their I/O and authentication. A peer's ``shutdown`` request
    ends only that peer; the private stdio wrapper closes its host separately.
    """

    def __init__(
        self,
        application: DeepCodeApplication,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        notification_capacity: int = 256,
    ) -> None:
        self.application = application
        self.max_message_bytes = max_message_bytes
        self.notification_capacity = notification_capacity
        self._lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._peers: set[RpcPeer] = set()
        self._resources = ExitStack()
        self._resources.callback(self._close_application)
        self._started = False
        self._closed = False
        self._application_closed = False

    def __enter__(self) -> ServiceHost:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        try:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Service host is closed")
                if self._started:
                    return
                app = self.application
                token = app.terminals.subscribe(self._publish)
                self._resources.callback(app.terminals.unsubscribe, token)
                token = app.skills.subscribe_changes(
                    lambda project_id: self._publish(
                        notifications.SKILLS_CHANGED, {"projectId": project_id}
                    )
                )
                self._resources.callback(app.skills.unsubscribe_changes, token)
                token = app.plugins.subscribe_changes(
                    lambda _discovery: self._publish(notifications.PLUGINS_CHANGED, {})
                )
                self._resources.callback(app.plugins.unsubscribe_changes, token)
                token = app.mcp.subscribe_changes(
                    lambda: self._publish(notifications.MCP_CHANGED, {})
                )
                self._resources.callback(app.mcp.unsubscribe_changes, token)
                watcher = ConfigFileWatcher(
                    app.settings.store,
                    lambda revision: self._publish(
                        notifications.SETTINGS_CHANGED, {"configRevision": revision}
                    ),
                )
                self._resources.callback(watcher.stop)
                watcher.start()
                self._started = True
        except BaseException:
            self.close()
            raise

    def connect(
        self,
        send: Callable[[bytes], None],
        *,
        service_info: dict[str, Any] | None = None,
    ) -> RpcPeer:
        """Attach a transport whose writer accepts complete encoded frames."""
        self.start()
        with self._lock:
            if self._closed:
                raise RuntimeError("Service host is closed")
            peer = RpcPeer(
                self.application,
                send,
                on_close=self._forget_peer,
                max_message_bytes=self.max_message_bytes,
                notification_capacity=self.notification_capacity,
                service_info=service_info,
            )
            self._peers.add(peer)
            try:
                peer.start()
            except BaseException:
                peer.close()
                raise
            return peer

    def close(self) -> None:
        with self._close_lock:
            with self._lock:
                if self._closed:
                    peers = ()
                    resources = None
                else:
                    self._closed = True
                    peers = tuple(self._peers)
                    self._peers.clear()
                    resources = self._resources.pop_all()
            if resources is None:
                if not self._application_closed:
                    self._close_application()
                return
            try:
                for peer in peers:
                    peer.close()
            finally:
                resources.close()

    def _close_application(self) -> None:
        self.application.close()
        self._application_closed = True

    def _forget_peer(self, peer: RpcPeer) -> None:
        with self._lock:
            self._peers.discard(peer)

    def _publish(self, method: str, payload: dict[str, Any]) -> None:
        with self._lock:
            peers = tuple(self._peers)
        for peer in peers:
            peer.notify(method, payload)
