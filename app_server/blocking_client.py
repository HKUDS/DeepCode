"""Synchronous native RPC bridge for existing CLI command handlers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time

from app_server.errors import RpcError
from app_server.native_client import NativeRpcClient
from app_server.service_client import ServiceOperationError, ServiceUnavailable
from app_server.service_state import ServiceFiles
from core.application.errors import (
    ApplicationError,
    ExpectedTurnMismatchError,
    NoActiveTurnError,
    TurnAlreadyRunningError,
    TurnNotSteerableError,
)


class RemoteApplicationError(ApplicationError):
    def __init__(self, error: RpcError):
        super().__init__(str(error), details=error.data.get("details"))
        self.code = error.stable_code
        self.retryable = error.data.get("retryable", False)


class BlockingServiceClient:
    """One owned I/O thread; close releases transport, never service work."""

    def __init__(
        self, files: ServiceFiles, *, surface: str = "cli", start: bool = True
    ):
        self.files = files
        self.surface = surface
        self._connection_lock = threading.RLock()
        self.generation = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, name="deepcode-cli-rpc", daemon=True
        )
        self.thread.start()
        self.client = NativeRpcClient(files)
        self._closed = False
        try:
            self.info = self.run(
                self.client.connect(
                    {
                        "protocolVersion": "1.0",
                        "clientInfo": {
                            "name": "deepcode-cli",
                            "version": "1",
                            "surface": surface,
                        },
                    },
                    start=start,
                )
            )
        except BaseException:
            self.close()
            raise

    def run(self, coroutine, *, timeout: float = 40):
        if self._closed:
            coroutine.close()
            raise RuntimeError("Service client is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError(
                "Service request timed out; inspect its state before retrying"
            ) from None
        except (ServiceUnavailable, ServiceOperationError) as exc:
            raise RemoteApplicationError(
                RpcError(-32000, str(exc), stable_code="SERVICE_UNAVAILABLE")
            ) from exc
        except RpcError as exc:
            details = exc.data.get("details") or {}
            if exc.stable_code == ExpectedTurnMismatchError.code:
                raise ExpectedTurnMismatchError(
                    details.get("expectedTurnId", "unknown"),
                    details.get("actualTurnId"),
                ) from exc
            for error_type in (
                NoActiveTurnError,
                TurnAlreadyRunningError,
                TurnNotSteerableError,
            ):
                if exc.stable_code == error_type.code:
                    raise error_type(str(exc), details=details) from exc
            raise RemoteApplicationError(exc) from exc

    def reconnect(self, previous=None):
        with self._connection_lock:
            if self._closed:
                raise RuntimeError("Service client is closed")
            if previous is not None and self.client is not previous:
                return
            old = self.client
            self.run(old.close())
            candidate = NativeRpcClient(self.files)
            info = self.run(
                candidate.connect(
                    {
                        "protocolVersion": "1.0",
                        "clientInfo": {
                            "name": "deepcode-cli",
                            "version": "1",
                            "surface": self.surface,
                        },
                    }
                )
            )
            self.client = candidate
            self.info = info
            self.generation += 1

    def call(self, method: str, params: dict):
        original = json.loads(json.dumps(params))
        policy = self.info.get("capabilities", {}).get("requestRetry", {})
        key = policy.get("keyedMethods", {}).get(method)
        safe = method in policy.get("readMethods", []) or (
            key and isinstance(original.get(key), str) and bool(original[key].strip())
        )
        for attempt in range(3):
            current = self.client
            try:
                if current.closed.is_set() and safe:
                    self.reconnect(current)
                latest = self.info.get("capabilities", {}).get("requestRetry", {})
                if attempt and not (
                    method in latest.get("readMethods", [])
                    or (key and latest.get("keyedMethods", {}).get(method) == key)
                ):
                    raise RuntimeError(
                        "Service retry policy changed; inspect the original operation before retrying"
                    )
                return self.run(
                    self.client.request(method, original),
                    timeout=125 if method == "provider/test" else 40,
                )
            except RemoteApplicationError as exc:
                if (
                    not safe
                    or exc.code
                    not in {
                        "CONNECTION_LOST",
                        "RESULT_UNKNOWN",
                        "NOT_CONNECTED",
                        "INPUT_DELIVERY_PENDING",
                    }
                    or attempt == 2
                ):
                    raise
                if exc.code != "INPUT_DELIVERY_PENDING":
                    self.reconnect(current)
                time.sleep(0.1 * (attempt + 1))

    def close(self):
        with self._connection_lock:
            if self._closed:
                return
            try:
                self.run(self.client.close())
            finally:
                self._closed = True
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(timeout=2)
                if not self.thread.is_alive():
                    self.loop.close()
