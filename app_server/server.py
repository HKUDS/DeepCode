"""Private stdio host and stream adapter for the shared RPC implementation."""

from __future__ import annotations

from typing import BinaryIO

from app_server.host import ServiceHost
from app_server.peer import RpcPeer
from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES
from core.application.application import DeepCodeApplication


def serve_stdio(peer: RpcPeer, source: BinaryIO) -> int:
    """Read one connection until EOF/shutdown; release only that connection."""
    try:
        while not peer.closed:
            raw = source.readline(peer.max_message_bytes + 1)
            if not raw:
                break
            if len(raw) > peer.max_message_bytes:
                chunk = raw
                while chunk and not chunk.endswith(b"\n"):
                    chunk = source.readline(64 * 1024)
                peer.reject_oversized_frame()
                continue
            peer.receive(raw)
    finally:
        peer.close()
    return 0


class AppServer:
    """Preserve the dedicated stdio process's EOF/shutdown ownership contract."""

    def __init__(
        self,
        application: DeepCodeApplication,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        self.application = application
        self.max_message_bytes = max_message_bytes

    def serve(self, source: BinaryIO, sink: BinaryIO) -> int:
        def send(encoded: bytes) -> None:
            sink.write(encoded)
            sink.flush()

        with ServiceHost(
            self.application, max_message_bytes=self.max_message_bytes
        ) as host:
            return serve_stdio(host.connect(send), source)
