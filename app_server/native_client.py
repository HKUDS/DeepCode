"""Authenticated service RPC for native clients; never owns an application."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

import aiohttp

from app_server.errors import RpcError
from app_server.protocol.codec import DEFAULT_MAX_MESSAGE_BYTES, encode_message
from app_server.service_client import (
    ServiceClient,
    ServiceOperationError,
    ServiceUnavailable,
)
from app_server.service_state import ServiceFiles, ServiceRecord
from core.version import __version__

logger = logging.getLogger(__name__)


# Requests that wait on a model or an external process (a provider probe, a
# /compact summary, an MCP server start that may take up to its configured
# 300s) legitimately outlast the default budget; the server keeps working on
# them either way, so timing out early only hides a result that is coming.
_SLOW_METHOD_TIMEOUTS = {
    "provider/test": 120.0,
    "thread/context/compact": 600.0,
    "mcp/probe": 330.0,
}
_DEFAULT_TIMEOUT = 25.0


def request_timeout(method: str) -> float:
    return _SLOW_METHOD_TIMEOUTS.get(method, _DEFAULT_TIMEOUT)


_RESTART_YOURSELF = (
    "Finish active work, then run `deepcode service restart` to start the "
    "service from this installation."
)


def _release(version: str) -> tuple[int, ...] | None:
    """A plain release's numbers; development and other builds never compare."""
    if re.fullmatch(r"\d+(?:\.\d+)*", version) is None:
        return None
    return tuple(int(part) for part in version.split("."))


def _version_mismatch(service_version: str, advice: str) -> RpcError:
    return RpcError(
        -32003,
        "The running service and this client have different versions "
        f"(service {service_version}, client {__version__}). {advice}",
        stable_code="SERVICE_VERSION_MISMATCH",
    )


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
        record, token, status = await self._discover()
        if record.version != __version__:
            await self._restart_older_service(record, status, allowed=start)
            record, token, _ = await self._discover()
            if record.version != __version__:
                raise _version_mismatch(
                    record.version,
                    "Its service manager still starts that version; run "
                    "`deepcode service install --at-login` from this installation.",
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

    async def _discover(self) -> tuple[ServiceRecord, str, dict]:
        # Authenticate the discovered listener before releasing any native secret.
        status = await asyncio.to_thread(ServiceClient(self.files).call, "status")
        found = self.files.read()
        if found is None or found[0].instance_id != status["instanceId"]:
            raise ServiceUnavailable("Service changed while connecting; reconnect")
        return *found, status

    async def _restart_older_service(
        self, record: ServiceRecord, status: dict, *, allowed: bool
    ) -> None:
        """Restart an idle service of an older release from this installation.

        Never a newer one, whose database this installation may not be able to
        open, and never one with work in progress. Raises unless it restarted.
        """
        ours, theirs = _release(__version__), _release(record.version)
        if ours is not None and theirs is not None and ours < theirs:
            raise _version_mismatch(
                record.version,
                "This client is older; update or restart it to use the service.",
            )
        if not allowed or ours is None or theirs is None:
            raise _version_mismatch(record.version, _RESTART_YOURSELF)
        if status["activeTurns"] or status["queuedTurns"] or status["terminals"]:
            raise _version_mismatch(
                record.version,
                "The service has active work, so it was not restarted; reconnect "
                "when that work finishes, or run `deepcode service restart` to "
                "wait for it.",
            )
        from cli.service_cli import restart_service

        logger.info(
            "Restarting the idle DeepCode %s service from this installation (%s)",
            record.version,
            __version__,
        )
        try:
            await asyncio.to_thread(
                restart_service, self.files, port=None, timeout=10, cancel_running=False
            )
        except (ServiceUnavailable, ServiceOperationError) as exc:
            raise _version_mismatch(
                record.version, f"Restarting it from this installation failed: {exc}"
            ) from exc

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
        budget = timeout if timeout is not None else request_timeout(method)
        try:
            async with asyncio.timeout(budget):
                # Once writing starts, failure cannot prove non-admission.
                sent = True
                await socket.send_str(encoded.decode())
                return await future
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as exc:
            policy = (self.info or {}).get("capabilities", {}).get("requestRetry", {})
            read_only = (
                method in policy.get("readMethods", []) or method == "initialize"
            )
            known = read_only or not sent
            # A timeout is a slow service, not a lost connection: the socket
            # is still good and the answer may still come. Say so, and mark
            # it so callers neither reconnect nor send it again.
            timed_out = isinstance(exc, TimeoutError)
            if not timed_out:
                message = (
                    "Service response was lost. Reconnect and inspect the "
                    "current state before retrying."
                )
            elif known:
                message = (
                    f"The DeepCode service did not answer within {budget:g}s; "
                    "it may be busy. Try again in a moment."
                )
            else:
                message = (
                    f"The DeepCode service did not confirm this request within "
                    f"{budget:g}s; it may still complete. Check the current "
                    "state before retrying."
                )
            raise RpcError(
                -32000,
                message,
                stable_code="CONNECTION_LOST" if known else "RESULT_UNKNOWN",
                data={"retryable": read_only, "timedOut": timed_out},
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
