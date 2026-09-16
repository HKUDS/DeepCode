"""OpenRouter's PKCE-to-API-key login, with user-private account binding.

OpenRouter issues a user-controlled key, not a refresh token. This adapter
therefore never invents refresh or remote-revocation operations.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from aiohttp import web

from core.providers.credentials import CredentialStore

FLOW_TTL = 300


@dataclass(slots=True)
class _Flow:
    id: str
    connection_id: str
    generation: str
    expires_at: float
    status: str = "starting"
    url: str | None = None
    error: str | None = None
    account_id: str | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class ProviderOAuthManager:
    def __init__(self, credentials: CredentialStore):
        self.credentials = credentials
        self._flows: dict[str, _Flow] = {}
        self._lock = threading.RLock()
        self._closed = False

    def start(self, connection_id: str, *, open_browser: bool = False) -> dict:
        with self._lock:
            if self._closed:
                raise ValueError("Provider login service is closed")
            self._flows = {
                key: flow
                for key, flow in self._flows.items()
                if flow.expires_at + 300 > time.monotonic()
            }
            active = [
                flow
                for flow in self._flows.values()
                if flow.status in {"starting", "pending", "exchanging"}
            ]
            if len(active) >= 8:
                raise ValueError(
                    "Too many pending provider logins; cancel a login first"
                )
            for flow in active:
                if flow.connection_id == connection_id:
                    self.cancel(flow.id)
            flow = _Flow(
                secrets.token_urlsafe(24),
                connection_id,
                self.credentials.begin_login(connection_id),
                time.monotonic() + FLOW_TTL,
            )
            self._flows[flow.id] = flow
            # Keep a bounded terminal history without dropping active sockets.
            terminal = [
                key
                for key, item in self._flows.items()
                if item.status not in {"starting", "pending", "exchanging"}
            ]
            for key in terminal[:-24]:
                self._flows.pop(key)
            flow.thread = threading.Thread(
                target=self._run,
                args=(flow,),
                name="deepcode-provider-login",
                daemon=True,
            )
            flow.thread.start()
        if not flow.ready.wait(5):
            self.cancel(flow.id)
            raise ValueError("The local provider login callback did not start")
        result = self.poll(flow.id)
        if open_browser and result["authorizationUrl"]:
            webbrowser.open(result["authorizationUrl"])
        return result

    def poll(self, flow_id: str) -> dict:
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                raise ValueError("Unknown or expired provider login")
            return {
                "flowId": flow.id,
                "connectionId": flow.connection_id,
                "provider": "openrouter",
                "status": flow.status,
                "authorizationUrl": flow.url if flow.status == "pending" else None,
                "expiresInSeconds": max(0, int(flow.expires_at - time.monotonic())),
                "error": flow.error,
                "accountId": flow.account_id,
                "refreshSupported": False,
            }

    def cancel(self, flow_id: str) -> dict:
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                raise ValueError("Unknown or expired provider login")
            if flow.status in {"starting", "pending", "exchanging"}:
                self.credentials.cancel_login(flow.connection_id, flow.generation)
                flow.cancel.set()
                flow.status = "cancelled"
                flow.url = None
            return self.poll(flow_id)

    def close(self):
        with self._lock:
            self._closed = True
            flows = list(self._flows.values())
            for flow in flows:
                self.cancel(flow.id)
        deadline = time.monotonic() + 3
        for flow in flows:
            if flow.thread is not None:
                flow.thread.join(max(0, deadline - time.monotonic()))

    def _run(self, flow):
        try:
            asyncio.run(self._authorize(flow))
        except (
            Exception
        ):  # callback startup/transport errors must not expose codes or keys
            with self._lock:
                if flow.status != "cancelled":
                    flow.status = "failed"
                    flow.error = "Provider authorization could not be completed"
        finally:
            flow.url = None
            flow.ready.set()

    async def _authorize(self, flow):
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = secrets.token_urlsafe(32)
        callback_path = "/auth/provider/" + state
        done = asyncio.Event()
        exchange = None
        allowed_hosts = set()

        async def callback(request):
            nonlocal exchange
            if request.host not in allowed_hosts or request.remote != "127.0.0.1":
                raise web.HTTPForbidden()
            with self._lock:
                if flow.status != "pending" or flow.cancel.is_set():
                    raise web.HTTPConflict(text="This login is no longer pending")
                codes = request.query.getall("code", [])
                if "error" in request.query:
                    flow.status = "cancelled"
                    self.credentials.cancel_login(flow.connection_id, flow.generation)
                    done.set()
                    return web.Response(text="Authorization cancelled")
                if len(codes) != 1 or not 1 <= len(codes[0]) <= 4096:
                    raise web.HTTPBadRequest(text="Missing authorization code")
                flow.status = "exchanging"
            exchange = asyncio.current_task()
            try:
                async with httpx.AsyncClient(
                    timeout=20, follow_redirects=False
                ) as client:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/auth/keys",
                        json={
                            "code": codes[0],
                            "code_verifier": verifier,
                            "code_challenge_method": "S256",
                        },
                    )
                    response.raise_for_status()
                    if len(response.content) > 65536:
                        raise ValueError("Invalid authorization response")
                    value = response.json()
                key = value.get("key")
                account = value.get("user_id")
                if (
                    not isinstance(key, str)
                    or not key
                    or not isinstance(account, str)
                    or not account
                ):
                    raise ValueError(
                        "The provider returned no verifiable account identity"
                    )
                with self._lock:
                    if flow.cancel.is_set():
                        raise ValueError("This login was cancelled")
                    self.credentials.complete_login(
                        flow.connection_id,
                        flow.generation,
                        api_key=key,
                        account_id=account,
                    )
                    flow.account_id = account
                    flow.status = "authenticated"
            except ValueError as exc:
                with self._lock:
                    if flow.status != "cancelled":
                        flow.status = "failed"
                        # Only our credential-store errors are projected; never an HTTP body.
                        flow.error = (
                            str(exc)
                            if str(exc)
                            in {
                                "This login was cancelled or superseded",
                                "A different account was selected. Disconnect the existing account before switching.",
                                "The provider returned no verifiable account identity",
                            }
                            else "The provider returned an invalid authorization response"
                        )
            except Exception:
                with self._lock:
                    if flow.status != "cancelled":
                        flow.status = "failed"
                        flow.error = (
                            "The provider rejected or could not complete authorization"
                        )
            finally:
                done.set()
            nonce = secrets.token_urlsafe(18)
            return web.Response(
                content_type="text/html",
                text=f'<!doctype html><meta charset="utf-8"><title>DeepCode</title><p>Authorization processed. Return to DeepCode to check its status.</p><script nonce="{nonce}">history.replaceState(null,"","/auth/provider/complete")</script>',
                headers={
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'none'",
                },
            )

        app = web.Application(client_max_size=8192)
        app.router.add_get(callback_path, callback)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            allowed_hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
            url = "https://openrouter.ai/auth?" + urlencode(
                {
                    "callback_url": f"http://localhost:{port}{callback_path}",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
            with self._lock:
                if not flow.cancel.is_set():
                    flow.url = url
                    flow.status = "pending"
                flow.ready.set()
            while (
                not done.is_set()
                and not flow.cancel.is_set()
                and time.monotonic() < flow.expires_at
            ):
                await asyncio.sleep(0.1)
            with self._lock:
                if flow.status in {"pending", "exchanging"}:
                    flow.status = "expired" if not flow.cancel.is_set() else "cancelled"
                    self.credentials.cancel_login(flow.connection_id, flow.generation)
            if (
                exchange is not None
                and not exchange.done()
                and (flow.cancel.is_set() or flow.status == "expired")
            ):
                exchange.cancel()
            if done.is_set():
                await asyncio.sleep(0.1)  # allow the bounded callback response to flush
        finally:
            await runner.cleanup()
