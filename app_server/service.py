"""Loopback management and business listener for one DeepCode application."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import threading
import time

from aiohttp import web

from app_server.browser_auth import BrowserAuth
from app_server.host import ServiceHost
from app_server.errors import RpcError
from app_server.protocol.codec import decode_request
from app_server.service_client import ServiceClient, ServiceUnavailable
from app_server.service_state import ServiceFiles, ServiceRecord, identity_proof
from app_server.websocket import WebSocketTransport
from app_server.web_surface import WebSurface
from core.application.application import DeepCodeApplication
from core.application.service_lifecycle import ServiceLifecycle
from core.config import LoggerConfig
from core.observability import setup_logging
from core.persistence.database import default_database_path
from core.private_storage import ensure_private_directory, open_private_file

logger = logging.getLogger(__name__)
MAX_CONTROL_BYTES = 16_384


class _PrivateRotatingLog(RotatingFileHandler):
    def _open(self):
        descriptor = open_private_file(
            Path(self.baseFilename), os.O_WRONLY | os.O_CREAT | os.O_APPEND
        )
        return os.fdopen(descriptor, "a", encoding=self.encoding)


class ControlServer:
    """Compose management and business transports with separate authorization."""

    def __init__(self, host: ServiceHost, record: ServiceRecord, token: str) -> None:
        self.host = host
        self.record = record
        self._token = token
        self.lifecycle = ServiceLifecycle(host.application)
        self.stopped = asyncio.Event()
        self.interrupt = threading.Event()
        self.phase = "ready"
        self._operation = asyncio.Lock()
        self.browser_auth = BrowserAuth(record.instance_id)
        self.web_surface = WebSurface(
            host.application, self.browser_auth, lambda: self.phase
        )
        self.business = WebSocketTransport(
            host,
            self.browser_auth,
            native_authenticated=self._authenticated,
            phase=lambda: self.phase,
            service_info={
                "instanceId": record.instance_id,
                "schemaVersion": host.application.database.schema_version(),
                "transport": "websocket",
                "shutdownScope": "connection",
                "frontendBuildId": (self.web_surface.build() or {}).get("buildId"),
            },
        )

    def application(self) -> web.Application:
        app = web.Application(
            client_max_size=MAX_CONTROL_BYTES, middlewares=[self._local_only]
        )
        app.add_routes(
            [
                web.get("/health/live", self._health),
                web.get("/health/ready", self._health),
                web.get("/control/identity", self._identity),
                web.post("/control/rpc", self._rpc),
                web.post("/auth/exchange", self._exchange),
                web.post("/auth/logout", self._logout),
                web.get("/api/rpc", self.business.handle),
            ]
        )
        app.add_routes(self.web_surface.routes())
        app.on_shutdown.append(self.business.shutdown)
        app.on_cleanup.append(self.business.cleanup)
        app.on_response_prepare.append(self._private_response)
        return app

    @web.middleware
    async def _local_only(self, request: web.Request, handler):
        if request.headers.getall("Host", []) != [f"127.0.0.1:{self.record.port}"]:
            raise web.HTTPForbidden(text="Local service management only")
        origins = request.headers.getall("Origin", [])
        browser_route = request.path.startswith(
            ("/auth/", "/api/", "/assets/")
        ) or request.path in {"/", "/index.html", "/web-build.json"}
        if origins and (not browser_route or origins != [self.record.url]):
            raise web.HTTPForbidden(text="Invalid browser origin")
        if request.path.startswith("/auth/") and not origins:
            raise web.HTTPForbidden(text="Browser origin required")
        if (
            request.path.startswith("/api/")
            and request.method not in {"GET", "HEAD"}
            and origins != [self.record.url]
        ):
            raise web.HTTPForbidden(text="Browser origin required")
        return await handler(request)

    async def _private_response(self, _request, response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self' ws://127.0.0.1:{self.record.port}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )

    async def _exchange(self, request: web.Request) -> web.Response:
        if self.phase != "ready":
            raise web.HTTPServiceUnavailable(text="Service is not ready")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="JSON body required")
        try:
            body = await request.json()
        except (ValueError, RecursionError):
            raise web.HTTPBadRequest(text="Invalid exchange body") from None
        if not isinstance(body, dict) or set(body) != {"ticket"}:
            raise web.HTTPBadRequest(text="Expected a browser ticket")
        session = self.browser_auth.exchange(body["ticket"])
        response = web.json_response(
            {"authenticated": True, "instanceId": self.record.instance_id}
        )
        response.set_cookie(
            self.browser_auth.cookie_name,
            session,
            httponly=True,
            samesite="Strict",
            max_age=self.browser_auth.SESSION_TTL,
            path="/",
        )
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        session = self.browser_auth.require(request)
        await self.business.revoke(session)
        response = web.json_response({"authenticated": False})
        response.del_cookie(self.browser_auth.cookie_name, path="/")
        return response

    async def _health(self, request: web.Request) -> web.Response:
        ready = self.phase == "ready"
        status = 200 if request.path == "/health/live" or ready else 503
        return web.json_response({"status": self.phase}, status=status)

    async def _identity(self, request: web.Request) -> web.Response:
        challenge = request.query.get("challenge", "")
        if len(challenge) != 32 or any(
            char not in "0123456789abcdef" for char in challenge
        ):
            raise web.HTTPBadRequest(text="Invalid identity challenge")
        return web.json_response(
            {"proof": identity_proof(self._token, self.record.instance_id, challenge)}
        )

    async def status(self) -> dict:
        return {
            "instanceId": self.record.instance_id,
            "phase": self.phase,
            "pid": self.record.pid,
            "url": self.record.url,
            "version": self.record.version,
            "protocolVersion": self.record.protocol_version,
            **await asyncio.to_thread(self.lifecycle.activity),
        }

    def _authenticated(self, request: web.Request) -> bool:
        return (
            hmac.compare_digest(
                request.headers.get("Authorization", "").encode(),
                f"Bearer {self._token}".encode(),
            )
            and request.headers.get("X-DeepCode-Instance") == self.record.instance_id
        )

    async def _rpc(self, request: web.Request) -> web.Response:
        if not self._authenticated(request):
            raise web.HTTPUnauthorized(text="Invalid service credential")
        try:
            rpc = decode_request(await request.read(), max_bytes=MAX_CONTROL_BYTES)
        except RpcError as exc:
            return web.json_response(
                {"jsonrpc": "2.0", "id": None, "error": exc.payload()}, status=400
            )
        if not rpc.has_id:
            raise web.HTTPBadRequest(text="Service control requests require an id")
        request_id = rpc.id
        params = rpc.params
        method = rpc.method
        if method == "auth/issue" and not params:
            if self.phase != "ready":
                return self._error(request_id, "Service is not ready")
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "instanceId": self.record.instance_id,
                        **self.browser_auth.issue(),
                    },
                }
            )
        if method == "status" and not params:
            return web.json_response(
                {"jsonrpc": "2.0", "id": request_id, "result": await self.status()}
            )
        if method == "resume" and not params:
            if self.phase != "drained" or self._operation.locked():
                return self._error(
                    request_id, "Service is not awaiting supervisor stop"
                )
            async with self._operation:
                await asyncio.to_thread(self.lifecycle.resume)
                self.phase = "ready"
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"instanceId": self.record.instance_id, "accepted": True},
                }
            )
        if method not in {"stop", "drain"}:
            return self._error(request_id, "Unknown service operation")
        timeout = params.get("timeout", 60.0)
        cancel = params.get("cancelRunning", False)
        if (
            set(params) - {"timeout", "cancelRunning"}
            or type(cancel) is not bool
            or (method == "drain" and cancel)
            or type(timeout) not in (int, float)
            or not 0 <= timeout <= 300
            or not math.isfinite(timeout)
        ):
            return self._error(request_id, "Invalid stop options")
        if self._operation.locked() or self.phase not in {"ready", "drained"}:
            return self._error(
                request_id, "Another service stop is already in progress"
            )
        async with self._operation:
            previous_phase = self.phase
            self.phase = "draining"
            deadline = time.monotonic() + timeout
            try:
                if not await self.business.wait_idle(timeout):
                    return self._error(
                        request_id, "RPC drain timed out; service is still running"
                    )
                if not cancel and not await asyncio.to_thread(
                    self.lifecycle.drain,
                    max(0.0, deadline - time.monotonic()),
                    self.interrupt,
                ):
                    return self._error(
                        request_id,
                        "Drain timed out; service is still running. Finish active work or explicitly cancel it.",
                    )
                self.phase = "drained" if method == "drain" else "stopping"
                response = web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "instanceId": self.record.instance_id,
                            "accepted": True,
                        },
                    }
                )
                if method == "drain":
                    return response
                try:
                    await response.prepare(request)
                    await response.write_eof()
                finally:
                    self.stopped.set()
                return response
            finally:
                if self.phase == "draining":
                    self.phase = previous_phase

    @staticmethod
    def _error(request_id: int | str | None, message: str) -> web.Response:
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": message},
            }
        )


async def serve(files: ServiceFiles, port: int) -> None:
    lease = files.acquire()
    # A status probe briefly takes this lock too. Distinguish it from a real
    # lifetime owner without waiting behind another running service forever.
    for _ in range(10):
        if lease is not None:
            break
        await asyncio.sleep(0.02)
        lease = files.acquire()
    if lease is None:
        # Crash records survive their owner. A successful exit suppresses native
        # supervisor recovery, so only an authenticated live owner permits it.
        try:
            await asyncio.to_thread(ServiceClient(files).call, "status", timeout=1)
        except ServiceUnavailable as exc:
            raise ServiceUnavailable(
                "Service ownership is busy but no live owner could be verified"
            ) from exc
        logger.info("A DeepCode service already owns this database")
        return
    host = None
    runner = None
    control = None
    installed_signals = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    loop = asyncio.get_running_loop()
    try:
        files.clear()
        if os.name != "nt":
            # Rebind after our old TCP connections enter TIME_WAIT. Do not use
            # SO_REUSEPORT or Windows address sharing with another live listener.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            logger.error("Cannot listen on 127.0.0.1:%s: %s", port, exc.strerror)
            raise
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        record = ServiceRecord(
            secrets.token_hex(16),
            str(files.database),
            os.getpid(),
            listener.getsockname()[1],
        )
        token = secrets.token_hex(32)
        application = await asyncio.to_thread(
            DeepCodeApplication.open,
            files.database,
            host_surface="service",
            run_automation_scheduler=True,
        )
        host = ServiceHost(application)
        from core.private_storage import atomic_write_private_json

        atomic_write_private_json(
            files.directory / "state-layout.json",
            {
                "schemaVersion": 1,
                "database": str(files.database),
                "sessions": str(application.session_store.root),
                "config": str(application.llm.config_store.path),
                "credentials": str(application.credentials.path),
                "revisions": str(
                    application.credentials.path.parent / "provider_revisions"
                ),
            },
        )
        await asyncio.to_thread(host.start)
        control = ControlServer(host, record, token)
        runner = web.AppRunner(
            control.application(), access_log=None, shutdown_timeout=10
        )
        await runner.setup()
        await web.SockSite(runner, listener).start()

        def stop() -> None:
            control.interrupt.set()
            control.stopped.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop)
            except NotImplementedError:
                # Windows asyncio uses the process's ordinary signal path.
                continue
            installed_signals.append(sig)
        files.publish(record, token)
        logger.info("Service ready at %s (pid %d)", record.url, record.pid)
        await control.stopped.wait()
    finally:
        if control is not None:
            control.interrupt.set()
            control.phase = "stopping"
        try:
            if runner is not None:
                await runner.cleanup()
        finally:
            try:
                if host is not None:
                    await asyncio.to_thread(host.close)
                files.clear()
                lease.close()
            finally:
                listener.close()
                for sig in installed_signals:
                    loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the DeepCode service in the foreground"
    )
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--port", type=int, default=3081)
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Keep the service attached to this terminal (default)",
    )
    parser.add_argument("--log-file", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    files = ServiceFiles(args.database or default_database_path())
    if args.log_file:
        ensure_private_directory(files.directory)
        handler = _PrivateRotatingLog(
            files.log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    else:
        handler = None
    setup_logging(
        LoggerConfig(
            level=os.environ.get("DEEPCODE_LOG_LEVEL", "INFO"), transports=["console"]
        ),
        force=True,
        console_sink=handler,
    )
    try:
        asyncio.run(serve(files, args.port))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception("DeepCode service failed (%s): %s", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
