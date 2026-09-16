"""Native stdio attachment to a shared service; EOF releases only this client."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import os
import select
import threading
from typing import BinaryIO

from app_server.errors import NotInitialized, RpcError
from app_server.native_client import NativeRpcClient
from app_server.protocol.codec import (
    DEFAULT_MAX_MESSAGE_BYTES,
    decode_request,
    encode_message,
)
from app_server.protocol.models import Response
from app_server.service_state import ServiceFiles


def serve_relay(files: ServiceFiles, source: BinaryIO, sink: BinaryIO) -> int:
    """Bounded pipes keep slow/closed Desktop clients from retaining the service."""
    incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=32)
    outgoing: queue.Queue[bytes | None] = queue.Queue(maxsize=256)
    output_lock = threading.Lock()
    output_bytes = 0
    stopped = threading.Event()
    failed = threading.Event()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_forever, name="deepcode-native-rpc", daemon=True
    )
    thread.start()
    client = NativeRpcClient(files, notify=lambda message: emit(message))

    def emit(message: dict) -> None:
        nonlocal output_bytes
        try:
            frame = encode_message(message)
            if len(frame) > DEFAULT_MAX_MESSAGE_BYTES:
                raise ValueError("Response exceeds the message limit")
            with output_lock:
                if output_bytes + len(frame) > 8 * 1024 * 1024:
                    raise queue.Full
                outgoing.put_nowait(frame)
                output_bytes += len(frame)
        except (queue.Full, ValueError):
            failed.set()

    def read() -> None:
        try:
            for raw in _input_frames(source, stopped):
                if len(raw) > DEFAULT_MAX_MESSAGE_BYTES:
                    failed.set()
                    return
                while not stopped.is_set():
                    try:
                        incoming.put(raw or None, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if not raw:
                    return
        except (OSError, ValueError):
            failed.set()

    def write() -> None:
        nonlocal output_bytes
        try:
            while True:
                frame = outgoing.get()
                if frame is None:
                    return
                sink.write(frame)
                sink.flush()
                with output_lock:
                    output_bytes -= len(frame)
        except (OSError, ValueError):
            failed.set()

    reader = threading.Thread(target=read, name="deepcode-native-stdin", daemon=True)
    writer = threading.Thread(target=write, name="deepcode-native-stdout", daemon=True)
    reader.start()
    writer.start()

    def run(coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=40)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RpcError(
                -32000, "Service connection timed out", stable_code="RESULT_UNKNOWN"
            ) from None

    try:
        initialized = False
        while not failed.is_set():
            if initialized and client.closed.is_set():
                # EOF tells the native bridge to offer Reconnect. Never start a
                # replacement application after a network failure.
                break
            try:
                raw = incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                break
            request = None
            try:
                request = decode_request(raw)
                if not initialized:
                    if request.method != "initialize":
                        raise NotInitialized()
                    result = run(client.connect(request.params, start=True))
                    initialized = True
                elif request.method == "shutdown":
                    result = {"accepted": True}
                elif request.method == "service/status":
                    from app_server.service_client import ServiceClient

                    if request.params:
                        raise RpcError(
                            -32602,
                            "Unexpected parameters",
                            stable_code="INVALID_REQUEST",
                        )
                    result = ServiceClient(files).call("status")
                elif request.method == "service/stop":
                    from cli.service_cli import stop_service

                    if request.params:
                        raise RpcError(
                            -32602,
                            "Unexpected parameters",
                            stable_code="INVALID_REQUEST",
                        )
                    # Native management only, bounded drain; no implicit cancel.
                    result = stop_service(files, timeout=10, cancel_running=False)
                else:
                    result = run(client.request(request.method, request.params))
                if request.has_id:
                    emit(Response(request.id, result=result).to_dict())
                if request.method == "shutdown":
                    break
            except RpcError as exc:
                if request is None or request.has_id:
                    emit(
                        Response(
                            request.id if request else None, error=exc.payload()
                        ).to_dict()
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                error = RpcError(-32000, str(exc), stable_code="SERVICE_UNAVAILABLE")
                emit(
                    Response(
                        request.id if request else None, error=error.payload()
                    ).to_dict()
                )
                break
        return 1 if failed.is_set() else 0
    finally:
        stopped.set()
        try:
            asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)
        except (concurrent.futures.TimeoutError, RuntimeError):
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        if not thread.is_alive():
            loop.close()
        try:
            outgoing.put(None, timeout=1)
        except queue.Full:
            pass
        writer.join(timeout=1)
        reader.join(timeout=1)


def _input_frames(source: BinaryIO, stopped: threading.Event):
    """Frame raw pipe reads without holding Python's buffered-stdin lock.

    A shutdown request may arrive while the parent keeps stdin open. A daemon
    blocked in BufferedReader.readline would abort Python during finalization.
    POSIX readiness polling also lets the reader exit without waiting for EOF.
    Windows raw ReadFile may remain blocked until process exit, but owns no
    Python buffered stream lock and does not prevent the relay from exiting.
    """
    try:
        descriptor = source.fileno()
    except (AttributeError, OSError, ValueError):
        while not stopped.is_set():
            frame = source.readline(DEFAULT_MAX_MESSAGE_BYTES + 1)
            yield frame
            if not frame:
                return
        return
    pending = bytearray()
    while not stopped.is_set():
        if os.name != "nt" and not select.select([descriptor], [], [], 0.1)[0]:
            continue
        chunk = os.read(
            descriptor, min(65536, DEFAULT_MAX_MESSAGE_BYTES + 1 - len(pending))
        )
        if not chunk:
            if pending:
                yield bytes(pending)
            yield b""
            return
        pending.extend(chunk)
        while (end := pending.find(b"\n")) >= 0:
            yield bytes(pending[: end + 1])
            del pending[: end + 1]
        if len(pending) > DEFAULT_MAX_MESSAGE_BYTES:
            raise ValueError("Input frame exceeds the message limit")
