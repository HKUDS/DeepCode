"""MCP OAuth storage and browser authorization built on the official SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from loguru import logger
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from core.file_lock import exclusive_file_lock
from core.mcp.connection import McpConnection
from core.mcp.models import McpConfigurationError, ResolvedMcpServer
from core.private_storage import (
    ensure_private_directory,
    open_existing_private_file,
    open_private_file,
)

MCP_OAUTH_CALLBACK_PATH = "/auth/mcp/callback"
_STORE_VERSION = 1
_FLOW_TTL_SECONDS = 300
_START_WAIT_SECONDS = 20


class McpAuthorizationRequiredError(RuntimeError):
    """A remote server needs a user-initiated browser authorization."""


class McpAuthorizationCancelledError(RuntimeError):
    """A browser authorization was cancelled or superseded."""


@dataclass(frozen=True, slots=True)
class McpOAuthHandlers:
    redirect_uri: str
    redirect_handler: Callable[[str], Awaitable[None]]
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]]
    reset_credentials: bool = False


def default_mcp_oauth_path() -> Path:
    configured = os.environ.get("DEEPCODE_HOME")
    home = Path(configured).expanduser() if configured else Path.home() / ".deepcode"
    return home / "auth" / "mcp.json"


class McpOAuthCredentialStore:
    """Atomic 0600 MCP OAuth storage isolated by server name and endpoint.

    A per-name generation prevents an old browser flow from recreating tokens
    after logout or after a server with the same name is replaced.
    """

    _thread_lock = threading.RLock()

    def __init__(
        self,
        server_name: str,
        server_url: str,
        *,
        path: Path | str | None = None,
    ) -> None:
        selected = Path(path) if path is not None else default_mcp_oauth_path()
        self.path = Path(os.path.abspath(os.fspath(selected.expanduser())))
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.server_name = server_name
        self.server_url_hash = hashlib.sha256(server_url.strip().encode()).hexdigest()
        self.identity = hashlib.sha256(
            f"{server_name}\0{self.server_url_hash}".encode()
        ).hexdigest()
        self._observed_generation = self._generation()

    async def get_tokens(self) -> OAuthToken | None:
        raw = await asyncio.to_thread(self._field, "tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except (TypeError, ValueError):
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        raw = tokens.model_dump(mode="json", exclude_none=True)
        await asyncio.to_thread(self._set_field, "tokens", raw)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = await asyncio.to_thread(self._field, "clientInfo")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except (TypeError, ValueError):
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        raw = client_info.model_dump(mode="json", exclude_none=True)
        await asyncio.to_thread(self._set_field, "clientInfo", raw)

    async def prepare(self, redirect_uri: str, *, reset: bool) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            self._require_current_generation(payload)
            entry = self._entry(payload, create=True)
            if reset:
                entry.pop("tokens", None)
                entry.pop("clientInfo", None)
            elif entry.get("redirectUri") != redirect_uri:
                # Dynamic registrations are bound to the redirect URI.
                entry.pop("clientInfo", None)
            entry["redirectUri"] = redirect_uri

        await asyncio.to_thread(self._mutate, mutate)

    async def redirect_uri(self) -> str | None:
        value = await asyncio.to_thread(self._field, "redirectUri")
        return value if isinstance(value, str) and value else None

    async def clear_tokens(self) -> None:
        await asyncio.to_thread(self._delete_field, "tokens")

    def has_tokens(self) -> bool:
        raw = self._field("tokens")
        return isinstance(raw, dict) and bool(raw.get("access_token"))

    def revision(self) -> str:
        try:
            stat = self.path.stat()
        except OSError:
            return "missing"
        raw = f"{self.path}:{stat.st_mtime_ns}:{stat.st_size}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @classmethod
    def delete_server(cls, server_name: str, *, path: Path | str | None = None) -> bool:
        selected = Path(path) if path is not None else default_mcp_oauth_path()
        target = Path(os.path.abspath(os.fspath(selected.expanduser())))
        lock_path = target.with_suffix(target.suffix + ".lock")
        removed = False
        with cls._thread_lock, exclusive_file_lock(lock_path):
            payload = _read_store(target)
            for identity, entry in tuple(payload["servers"].items()):
                if isinstance(entry, dict) and entry.get("name") == server_name:
                    payload["servers"].pop(identity, None)
                    removed = True
            payload["generations"][server_name] = secrets.token_urlsafe(24)
            _write_store(target, payload)
        return removed

    @classmethod
    def delete_endpoint(
        cls,
        server_name: str,
        server_url: str,
        *,
        path: Path | str | None = None,
    ) -> bool:
        """Remove credentials for one endpoint without deleting same-name overlays."""

        selected = Path(path) if path is not None else default_mcp_oauth_path()
        target = Path(os.path.abspath(os.fspath(selected.expanduser())))
        lock_path = target.with_suffix(target.suffix + ".lock")
        url_hash = hashlib.sha256(server_url.strip().encode()).hexdigest()
        identity = hashlib.sha256(f"{server_name}\0{url_hash}".encode()).hexdigest()
        with cls._thread_lock, exclusive_file_lock(lock_path):
            payload = _read_store(target)
            removed = payload["servers"].pop(identity, None) is not None
            payload["generations"][server_name] = secrets.token_urlsafe(24)
            _write_store(target, payload)
        return removed

    def _generation(self) -> str | None:
        with self._thread_lock, exclusive_file_lock(self.lock_path):
            return _read_store(self.path)["generations"].get(self.server_name)

    def _field(self, name: str) -> Any:
        with self._thread_lock, exclusive_file_lock(self.lock_path):
            payload = _read_store(self.path)
            if (
                payload["generations"].get(self.server_name)
                != self._observed_generation
            ):
                return None
            entry = self._entry(payload, create=False)
            return entry.get(name) if entry is not None else None

    def _set_field(self, name: str, value: Any) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            self._require_current_generation(payload)
            self._entry(payload, create=True)[name] = value

        self._mutate(mutate)

    def _delete_field(self, name: str) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            self._require_current_generation(payload)
            entry = self._entry(payload, create=False)
            if entry is not None:
                entry.pop(name, None)

        self._mutate(mutate)

    def _mutate(self, transform: Callable[[dict[str, Any]], None]) -> None:
        with self._thread_lock, exclusive_file_lock(self.lock_path):
            payload = _read_store(self.path)
            transform(payload)
            _write_store(self.path, payload)

    def _require_current_generation(self, payload: dict[str, Any]) -> None:
        if payload["generations"].get(self.server_name) != self._observed_generation:
            raise McpAuthorizationCancelledError("MCP authorization was superseded")

    def _entry(
        self,
        payload: dict[str, Any],
        *,
        create: bool,
    ) -> dict[str, Any] | None:
        entry = payload["servers"].get(self.identity)
        if isinstance(entry, dict) and entry.get("urlHash") == self.server_url_hash:
            return entry
        if not create:
            return None
        entry = {"name": self.server_name, "urlHash": self.server_url_hash}
        payload["servers"][self.identity] = entry
        return entry


async def create_mcp_oauth_provider(
    server: ResolvedMcpServer,
    handlers: McpOAuthHandlers | None = None,
) -> OAuthClientProvider:
    url = server.definition.url or ""
    storage = McpOAuthCredentialStore(server.name, url)
    if handlers is not None:
        await storage.prepare(
            handlers.redirect_uri,
            reset=handlers.reset_credentials,
        )
        redirect_uri = handlers.redirect_uri
        redirect_handler = handlers.redirect_handler
        callback_handler = handlers.callback_handler
    else:
        if not await asyncio.to_thread(storage.has_tokens):
            raise McpAuthorizationRequiredError(
                f"MCP server {server.name!r} requires browser authorization"
            )
        redirect_uri = (
            await storage.redirect_uri() or f"http://127.0.0.1{MCP_OAUTH_CALLBACK_PATH}"
        )

        async def authorization_required(_url: str) -> None:
            await storage.clear_tokens()
            raise McpAuthorizationRequiredError(
                f"MCP server {server.name!r} requires browser authorization"
            )

        async def missing_callback() -> tuple[str, str | None]:
            raise McpAuthorizationRequiredError(
                f"MCP server {server.name!r} requires browser authorization"
            )

        redirect_handler = authorization_required
        callback_handler = missing_callback

    metadata = OAuthClientMetadata(
        redirect_uris=[AnyUrl(redirect_uri)],
        token_endpoint_auth_method="none",
        client_name="DeepCode",
        client_uri=AnyHttpUrl("https://github.com/HKUDS/DeepCode"),
        software_id="https://github.com/HKUDS/DeepCode",
    )
    return OAuthClientProvider(
        url,
        metadata,
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=_FLOW_TTL_SECONDS,
    )


@dataclass(slots=True)
class _OAuthFlow:
    id: str
    server: ResolvedMcpServer
    expires_at: float
    status: str = "starting"
    authorization_url: str | None = None
    error: str | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


@dataclass(frozen=True, slots=True)
class McpOAuthFlowInfo:
    flow_id: str
    server_id: str
    name: str
    status: str
    authorization_url: str | None
    expires_in_seconds: int
    error: str | None


class McpOAuthManager:
    """Own user-initiated loopback OAuth flows outside any UI process layer."""

    def __init__(self) -> None:
        self._flows: dict[str, _OAuthFlow] = {}
        self._by_server: dict[str, str] = {}
        self._lock = threading.RLock()
        self._listeners: dict[str, Callable[[McpOAuthFlowInfo], None]] = {}

    def subscribe(self, listener: Callable[[McpOAuthFlowInfo], None]) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._listeners[token] = listener
        return token

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def start(
        self,
        server: ResolvedMcpServer,
        *,
        open_browser: bool = True,
        reset_credentials: bool = False,
    ) -> McpOAuthFlowInfo:
        if server.definition.auth != "oauth" or server.definition.type == "stdio":
            raise McpConfigurationError(
                "OAuth requires an OAuth-enabled HTTP MCP server"
            )
        self.cancel(server)
        flow = _OAuthFlow(
            id=secrets.token_urlsafe(24),
            server=server,
            expires_at=time.monotonic() + _FLOW_TTL_SECONDS,
        )
        with self._lock:
            self._flows[flow.id] = flow
            self._by_server[_flow_key(server)] = flow.id
        thread = threading.Thread(
            target=self._run,
            args=(flow, open_browser, reset_credentials),
            name=f"deepcode-mcp-oauth-{server.server_id}",
            daemon=True,
        )
        flow.thread = thread
        thread.start()
        flow.ready.wait(_START_WAIT_SECONDS)
        return self._info(flow)

    def status(self, server: ResolvedMcpServer) -> str:
        return self.status_for(
            server.server_id,
            server.name,
            server.definition.url or "",
        )

    def status_for(self, server_id: str, server_name: str, server_url: str) -> str:
        with self._lock:
            flow_id = self._by_server.get(_flow_key_values(server_id, server_url))
            flow = self._flows.get(flow_id) if flow_id is not None else None
        if flow is not None and flow.status in {
            "starting",
            "authorization_required",
            "connecting",
        }:
            return "authorizing"
        storage = McpOAuthCredentialStore(
            server_name,
            server_url,
        )
        return "authenticated" if storage.has_tokens() else "login_required"

    def wait(
        self, flow_id: str, *, timeout: float = _FLOW_TTL_SECONDS
    ) -> McpOAuthFlowInfo:
        with self._lock:
            flow = self._flows.get(flow_id)
        if flow is None:
            raise ValueError("unknown MCP OAuth flow")
        flow.done.wait(timeout)
        return self._info(flow)

    def logout(self, server: ResolvedMcpServer) -> bool:
        self.cancel(server)
        removed = McpOAuthCredentialStore.delete_server(server.name)
        self._notify(
            McpOAuthFlowInfo(
                "",
                server.server_id,
                server.name,
                "logged_out",
                None,
                0,
                None,
            )
        )
        return removed

    def cancel(self, server: ResolvedMcpServer) -> None:
        with self._lock:
            flow_id = self._by_server.get(_flow_key(server))
            flow = self._flows.get(flow_id) if flow_id is not None else None
        if flow is not None and flow.status not in {
            "authenticated",
            "failed",
            "cancelled",
        }:
            flow.cancel.set()
            flow.status = "cancelled"
            flow.ready.set()
            self._notify(self._info(flow))

    def close(self) -> None:
        """Cancel outstanding browser flows and release all listeners."""

        with self._lock:
            flows = tuple(self._flows.values())
        for flow in flows:
            if not flow.done.is_set():
                flow.cancel.set()
                flow.status = "cancelled"
                flow.ready.set()
        for flow in flows:
            thread = flow.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1)
        with self._lock:
            self._flows.clear()
            self._by_server.clear()
            self._listeners.clear()

    def _run(
        self,
        flow: _OAuthFlow,
        open_browser: bool,
        reset_credentials: bool,
    ) -> None:
        try:
            asyncio.run(self._authorize(flow, open_browser, reset_credentials))
        except BaseException as exc:  # noqa: BLE001 - worker boundary
            if flow.status != "cancelled":
                flow.status = "failed"
                flow.error = _safe_oauth_error(exc)
        finally:
            flow.ready.set()
            flow.done.set()
            self._notify(self._info(flow))

    async def _authorize(
        self,
        flow: _OAuthFlow,
        open_browser: bool,
        reset_credentials: bool,
    ) -> None:
        callback: asyncio.Future[tuple[str, str | None]] = (
            asyncio.get_running_loop().create_future()
        )
        callback_server = await asyncio.start_server(
            lambda reader, writer: _handle_callback(reader, writer, callback),
            "127.0.0.1",
            0,
        )
        socket = callback_server.sockets[0]
        port = int(socket.getsockname()[1])
        redirect_uri = f"http://127.0.0.1:{port}{MCP_OAUTH_CALLBACK_PATH}"

        async def receive_url(url: str) -> None:
            _validate_authorization_url(url)
            flow.authorization_url = url
            flow.status = "authorization_required"
            flow.ready.set()
            self._notify(self._info(flow))
            if open_browser:
                await asyncio.to_thread(webbrowser.open, url)

        async def receive_callback() -> tuple[str, str | None]:
            while True:
                if flow.cancel.is_set():
                    raise McpAuthorizationCancelledError("MCP authorization cancelled")
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(callback),
                        timeout=0.25,
                    )
                    flow.status = "connecting"
                    self._notify(self._info(flow))
                    return result
                except TimeoutError:
                    if time.monotonic() >= flow.expires_at:
                        raise TimeoutError("MCP authorization timed out")

        handlers = McpOAuthHandlers(
            redirect_uri,
            receive_url,
            receive_callback,
            reset_credentials,
        )
        connection = McpConnection(
            flow.server,
            oauth_provider_factory=lambda server: create_mcp_oauth_provider(
                server,
                handlers,
            ),
        )
        try:
            async with callback_server:
                await connection.start(timeout_seconds=_FLOW_TTL_SECONDS)
            flow.status = "authenticated"
        finally:
            callback_server.close()
            await callback_server.wait_closed()
            await connection.close()

    def _info(self, flow: _OAuthFlow) -> McpOAuthFlowInfo:
        return McpOAuthFlowInfo(
            flow.id,
            flow.server.server_id,
            flow.server.name,
            flow.status,
            flow.authorization_url,
            max(0, int(flow.expires_at - time.monotonic())),
            flow.error,
        )

    def _notify(self, info: McpOAuthFlowInfo) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener(info)
            except Exception:  # noqa: BLE001 - event listener boundary
                logger.exception("MCP OAuth listener failed")


async def _handle_callback(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    callback: asyncio.Future[tuple[str, str | None]],
) -> None:
    ok = False
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        if len(request) > 16_384:
            raise ValueError("OAuth callback request is too large")
        parts = request.decode("ascii", errors="strict").strip().split(" ")
        if len(parts) != 3 or parts[0] != "GET":
            raise ValueError("Invalid OAuth callback request")
        parsed = urlsplit(parts[1])
        if parsed.path != MCP_OAUTH_CALLBACK_PATH:
            raise ValueError("Invalid OAuth callback path")
        values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
        error = values.get("error", [None])[0]
        code = values.get("code", [None])[0]
        state = values.get("state", [None])[0]
        if error:
            raise ValueError(f"OAuth provider returned {str(error)[:80]}")
        if not code or len(code) > 8192 or not state or len(state) > 1024:
            raise ValueError("OAuth callback is missing code or state")
        if not callback.done():
            callback.set_result((code, state))
        ok = True
    except BaseException as exc:  # noqa: BLE001 - translate HTTP callback failure
        if not callback.done():
            callback.set_exception(exc)
    finally:
        body = (
            "DeepCode authorization received. You can close this window."
            if ok
            else "DeepCode could not accept this authorization callback."
        ).encode()
        status = b"200 OK" if ok else b"400 Bad Request"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()


def _validate_authorization_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.scheme not in {"https", "http"}
        or (
            parsed.scheme == "http"
            and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise McpConfigurationError("MCP server returned an unsafe OAuth URL")


def _flow_key(server: ResolvedMcpServer) -> str:
    return _flow_key_values(server.server_id, server.definition.url or "")


def _flow_key_values(server_id: str, server_url: str) -> str:
    raw = f"{server_id}\0{server_url}".encode()
    return hashlib.sha256(raw).hexdigest()


def _safe_oauth_error(exc: BaseException) -> str:
    if isinstance(exc, (McpAuthorizationCancelledError, TimeoutError)):
        return str(exc)[:240]
    return f"OAuth authorization failed ({type(exc).__name__})"


def _empty_store() -> dict[str, Any]:
    return {"version": _STORE_VERSION, "servers": {}, "generations": {}}


def _read_store(path: Path) -> dict[str, Any]:
    try:
        descriptor = open_existing_private_file(path)
    except FileNotFoundError:
        return _empty_store()
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (json.JSONDecodeError, OSError, TypeError):
        return _empty_store()
    if not isinstance(value, dict) or value.get("version", 1) != _STORE_VERSION:
        return _empty_store()
    servers = value.get("servers")
    generations = value.get("generations")
    if not isinstance(servers, dict) or not isinstance(generations, dict):
        return _empty_store()
    return {"version": _STORE_VERSION, "servers": servers, "generations": generations}


def _write_store(path: Path, value: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "MCP_OAUTH_CALLBACK_PATH",
    "McpAuthorizationCancelledError",
    "McpAuthorizationRequiredError",
    "McpOAuthCredentialStore",
    "McpOAuthFlowInfo",
    "McpOAuthHandlers",
    "McpOAuthManager",
    "create_mcp_oauth_provider",
    "default_mcp_oauth_path",
]
