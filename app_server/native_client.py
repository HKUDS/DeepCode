"""Authenticated service RPC for native clients; never owns an application."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import aiohttp

from app_server.errors import RpcError
from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES, encode_message
from app_server.service_client import ServiceClient, ServiceUnavailable
from app_server.service_state import ServiceFiles
from core.version import __version__


class NativeRpcClient:
    """One connection on one event loop; no implicit mutation replay or fallback."""

    def __init__(
        self, files: ServiceFiles, *, notify: Callable[[dict], None] | None = None
    ):
        self.files = files
        self.notify = notify or (lambda _: None)
        self.info: dict[str, Any] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._serial = 0
        self.closed = asyncio.Event()

    async def connect(
        self, initialize: dict, *, start: bool = False, port: int | None = None
    ) -> dict:
        if self._session is not None:
            raise RuntimeError("connection already opened")
        if start:
            from cli.service_cli import start_service

            await asyncio.to_thread(start_service, self.files, port=port)
        # Authenticate the discovered listener before releasing any native secret.
        status = await asyncio.to_thread(ServiceClient(self.files).call, "status")
        found = self.files.read()
        if found is None or found[0].instance_id != status["instanceId"]:
            raise ServiceUnavailable("Service changed while connecting; reconnect")
        record, token = found
        if record.version != __version__:
            raise RpcError(
                -32003,
                "The running service and this client have different versions. "
                "Finish active work and upgrade/restart the service explicitly.",
                stable_code="SERVICE_VERSION_MISMATCH",
            )
        self._session = aiohttp.ClientSession(
            trust_env=False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
        )
        try:
            self._socket = await self._session.ws_connect(
                record.url + "/api/rpc",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-DeepCode-Instance": record.instance_id,
                },
                max_msg_size=DEFAULT_MAX_MESSAGE_BYTES,
                heartbeat=20,
            )
            self._reader = asyncio.create_task(self._read())
            self.info = await self.request("initialize", initialize)
            identity = self.info.get("serviceInfo", {})
            if (
                self.info.get("protocolVersion") != "1.0"
                or identity.get("instanceId") != record.instance_id
            ):
                raise ServiceUnavailable(
                    "Service handshake does not match the authenticated instance"
                )
            return self.info
        except BaseException:
            await self.close()
            raise

    async def request(
        self, method: str, params: dict, *, timeout: float | None = None
    ) -> Any:
        socket = self._socket
        if socket is None or socket.closed or self.closed.is_set():
            raise RpcError(
                -32000,
                "Service disconnected; request was not sent",
                stable_code="NOT_CONNECTED",
            )
        if len(self._pending) >= 64:
            raise RpcError(
                -32000, "Too many pending service requests", stable_code="CLIENT_BUSY"
            )
        self._serial += 1
        request_id = self._serial
        encoded = encode_message(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        if len(encoded) > DEFAULT_MAX_MESSAGE_BYTES:
            raise RpcError(
                -32600,
                "Request exceeds the service message limit",
                stable_code="INVALID_REQUEST",
            )
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        sent = False
        try:
            async with asyncio.timeout(
                timeout
                if timeout is not None
                else (120 if method == "provider/test" else 25)
            ):
                # Once writing starts, failure cannot prove non-admission.
                sent = True
                await socket.send_str(encoded.decode())
                return await future
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as exc:
            policy = (self.info or {}).get("capabilities", {}).get("requestRetry", {})
            read_only = (
                method in policy.get("readMethods", []) or method == "initialize"
            )
            raise RpcError(
                -32000,
                "Service response was lost. Reconnect and inspect the current state before retrying.",
                stable_code="CONNECTION_LOST"
                if read_only or not sent
                else "RESULT_UNKNOWN",
                data={"retryable": read_only},
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def _read(self) -> None:
        try:
            async for frame in self._socket:
                if frame.type != aiohttp.WSMsgType.TEXT:
                    break
                message = json.loads(frame.data)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("Invalid service frame")
                if "id" in message:
                    future = self._pending.get(message["id"])
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        error = message["error"]
                        future.set_exception(
                            RpcError(
                                error["code"],
                                error["message"],
                                stable_code=error.get("data", {}).get(
                                    "code", "RPC_ERROR"
                                ),
                                data=error.get("data"),
                            )
                        )
                    elif "result" in message:
                        future.set_result(message["result"])
                    else:
                        raise ValueError("Invalid service response")
                elif isinstance(message.get("method"), str):
                    self.notify(message)
                else:
                    raise ValueError("Invalid service notification")
        finally:
            self.closed.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Service connection closed"))

    async def close(self) -> None:
        self.closed.set()
        if self._socket is not None:
            try:
                await asyncio.wait_for(self._socket.close(), 3)
            except TimeoutError:
                pass
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._session is not None:
            await self._session.close()
