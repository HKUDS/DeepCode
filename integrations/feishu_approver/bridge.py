"""Core of the Feishu approval bridge.

Runs as a **separate process** that:

1. connects to a running DeepCode app_server over its native RPC (``NativeRpcClient``,
   authenticated via ``ServiceFiles`` discovery + bearer token, starting the
   service when it is down);
2. listens for the ``approval.requested`` live notification, which carries the
   durable ``approval_view``, and sweeps for approvals missed while the bridge
   was down;
3. pushes an interactive Feishu card (allow once / allow session / deny) to the
   allowlisted ``open_id`` owners via the Feishu long-lived websocket (no public IP);
4. translates a card click into an ``approval/respond`` RPC — the durable SQLite
   transition is authoritative, so a phone decision is exactly as valid as a
   terminal one.

Timeout policy (``FEISHU_APPROVER_TIMEOUT_ACTION``): an approval left
unanswered is auto-resolved after ``timeout_seconds`` (``0`` disables) —
``deny`` fails closed, ``approve_once``/``approve_session`` auto-默许 so an
unattended phone never stalls a running turn.  A *card delivery* failure
still denies immediately: infrastructure errors must not silently allow
risky operations through.  Clicks from users outside the ``open_id``
allowlist are ignored.  Cards carry only the opaque ``approval_id``.

``lark_oapi`` is imported lazily (it is slow to import) so this module stays
unit-testable with a mock connector.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app_server.native_client import NativeRpcClient
from app_server.service_state import ServiceFiles
from core.persistence.database import default_database_path
from integrations.feishu_approver.config import BridgeSettings

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0"

# "automation" marks this as a machine client rather than a human session.
_CLIENT_INFO = {"name": "feishu-approver", "version": "1", "surface": "automation"}

DECISION_APPROVE_ONCE = "approved_once"
DECISION_APPROVE_SESSION = "approved_session"
DECISION_DENY = "denied"

# Card button ``value.action`` -> decision string accepted by approval/respond.
_ACTIONS: dict[str, str] = {
    "approve_once": DECISION_APPROVE_ONCE,
    "approve_session": DECISION_APPROVE_SESSION,
    "deny": DECISION_DENY,
}

_DECISION_LABELS = {
    DECISION_APPROVE_ONCE: "Approved once",
    DECISION_APPROVE_SESSION: "Approved for session",
    DECISION_DENY: "Denied",
}

# settings.timeout_action -> decision sent when nobody answers in time.
_TIMEOUT_DECISIONS: dict[str, str] = {
    "deny": DECISION_DENY,
    "approve_once": DECISION_APPROVE_ONCE,
    "approve_session": DECISION_APPROVE_SESSION,
}


def _new_rpc_client(
    files: ServiceFiles, notify: Callable[[dict], None]
) -> NativeRpcClient:
    """Factory seam so tests can inject a fake RPC client."""
    return NativeRpcClient(files, notify=notify)


class ApprovalBridge:
    """Owns one RPC connection to app_server and one Feishu long connection."""

    def __init__(
        self,
        settings: BridgeSettings,
        *,
        client_factory: Callable[
            [ServiceFiles, Callable[[dict], None]], Any
        ] = _new_rpc_client,
    ):
        self.settings = settings
        self._client_factory = client_factory

        # approval_id -> {"approval": view, "messageId": str}.  Added when a
        # prompt card is sent, atomically taken on click/timeout/deny.
        self._pending: dict[str, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()

        # Timers for timeout auto-resolution, keyed by approval_id.
        self._timers: dict[str, asyncio.TimerHandle] = {}

        # Bridges the Feishu callback thread (sync) onto the RPC event loop.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._rpc: Any = None
        database = (
            Path(settings.database_path).expanduser()
            if settings.database_path
            else default_database_path()
        )
        self._files = ServiceFiles(database)
        self._lark_client: Any = None  # lark.Client (sync, for im.v1.message.create)
        self._ws: Any = None  # lark.ws.Client
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ RPC

    async def _rpc_request(self, method: str, params: dict[str, Any]) -> Any:
        """One app_server RPC round-trip."""
        rpc = self._rpc
        if rpc is None:
            raise RuntimeError("Feishu bridge RPC client not started")
        return await rpc.request(method, params)

    async def _send_message(
        self, receive_id_type: str, receive_id: str, msg_type: str, content: str
    ) -> str:
        """Create one Feishu message, return its id."""
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        return await asyncio.to_thread(self._create_message, receive_id_type, body)

    def _on_notify(self, message: dict[str, Any]) -> None:
        """Runs on the RPC read loop; must never raise."""
        try:
            method = message.get("method")
            params = message.get("params") or {}
            if method == "approval.requested":
                approval = (params.get("payload") or {}).get("approval") or {}
                if approval.get("status") != "pending":
                    return
                self._schedule(self._handle_approval(approval, params))
                return
        except Exception:
            logger.exception("feishu-approver: failed to handle notification")

    def _schedule(self, coro) -> None:
        if self._loop is None or self._loop.is_closed():
            coro.close()
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _connect_rpc(self) -> None:
        """Connect (starting the service if needed); notify drives live events."""
        initialize = {"protocolVersion": PROTOCOL_VERSION, "clientInfo": _CLIENT_INFO}
        self._rpc = self._client_factory(self._files, self._on_notify)
        info = await self._rpc.connect(initialize, start=True)
        logger.info(
            "feishu-approver: connected to app_server %s", info.get("serviceInfo")
        )

    async def _respond(
        self, approval_id: str, decision: str, note: str | None = None
    ) -> bool:
        """Send approval/respond. Returns False if already resolved elsewhere."""
        params: dict[str, Any] = {"approvalId": approval_id, "decision": decision}
        if note:
            params["message"] = note
        try:
            await self._rpc.request("approval/respond", params)
            return True
        except Exception as exc:  # noqa: BLE001 - a lost race surfaces as an RPC error
            logger.info(
                "feishu-approver: respond %s for %s failed (likely already resolved): %s",
                decision,
                approval_id,
                exc,
            )
            return False

    # ------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        """Run until cancelled: connect, sweep pending, then pump events."""
        self._loop = asyncio.get_running_loop()
        # Start Feishu first: once the RPC connection is up, approval.requested
        # can arrive at any moment, and a prompt must never race a missing
        # _lark_client (that would fail-closed deny a legitimate request).
        self._start_feishu()
        await self._connect_rpc()
        await self._sweep_pending()
        try:
            await self._stop.wait()
        finally:
            await self._close()

    def stop(self) -> None:
        """Ask the bridge to shut down (thread-safe)."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)

    async def _close(self) -> None:
        for timer in list(self._timers.values()):
            timer.cancel()
        self._timers.clear()
        if self._rpc is not None:
            try:
                await self._rpc.close()
            except Exception:
                logger.debug("feishu-approver: rpc close raised", exc_info=True)

    # ------------------------------------------------------------- sweeping

    async def _sweep_pending(self) -> None:
        """Recover approvals requested while the bridge was down."""
        try:
            threads = await self._rpc.request("thread/list", {})
            for thread in threads.get("threads", []):
                if thread.get("status") != "waiting":
                    continue
                turns = await self._rpc.request(
                    "turn/list", {"threadId": thread["id"], "state": "active"}
                )
                for turn in turns.get("turns", []):
                    snapshot = await self._rpc.request(
                        "turn/read", {"turnId": turn["id"]}
                    )
                    for approval in snapshot.get("approvals", []):
                        if approval.get("status") == "pending":
                            await self._handle_approval(approval, {})
        except Exception:
            logger.exception("feishu-approver: pending sweep failed")

    # ------------------------------------------------------------- approvals

    async def _handle_approval(
        self, approval: dict[str, Any], event: dict[str, Any]
    ) -> None:
        approval_id = str(approval.get("id") or "")
        if not approval_id:
            return
        with self._pending_lock:
            if approval_id in self._pending:
                return  # already prompted (sweep + live event race)
            self._pending[approval_id] = {
                "approval": approval,
                "threadId": event.get("threadId") or approval.get("threadId"),
            }
        try:
            await self._send_card(approval)
        except Exception:
            logger.exception("feishu-approver: failed to send card for %s", approval_id)
            with self._pending_lock:
                self._pending.pop(approval_id, None)
            # Fail closed so a cardless prompt does not hang the run.
            await self._respond(approval_id, DECISION_DENY, "card delivery failed")
            return
        self._arm_timeout(approval_id)

    def _arm_timeout(self, approval_id: str) -> None:
        """Auto-resolve via ``settings.timeout_action`` if nobody answers."""
        seconds = self.settings.timeout_seconds
        if seconds <= 0 or self._loop is None:
            return
        timer = self._loop.call_later(seconds, self._on_timeout, approval_id)
        with self._pending_lock:
            self._timers[approval_id] = timer

    def _take_pending(self, approval_id: str) -> dict[str, Any] | None:
        """Atomically pop a pending entry and cancel its timeout timer."""
        with self._pending_lock:
            entry = self._pending.pop(approval_id, None)
            timer = self._timers.pop(approval_id, None)
        if timer is not None:
            timer.cancel()
        return entry

    def _on_timeout(self, approval_id: str) -> None:
        """Runs on the event loop; apply the configured timeout action."""
        entry = self._take_pending(approval_id)
        if entry is None:
            return
        message_id = str(entry.get("messageId") or "")
        decision = _TIMEOUT_DECISIONS[self.settings.timeout_action]
        self._schedule(
            self._finish(
                approval_id, decision, "timeout", by="auto", message_id=message_id
            )
        )

    async def _finish(
        self,
        approval_id: str,
        decision: str,
        note: str,
        *,
        by: str,
        message_id: str = "",
    ) -> bool:
        """Record a decision and (best-effort) patch the card to show it."""
        ok = await self._respond(approval_id, decision, note)
        logger.info(
            "feishu-approver: decision %s recorded for %s (ok=%s) by=%s",
            decision,
            approval_id,
            ok,
            by,
        )
        if ok and message_id:
            try:
                await self._patch_card(message_id, approval_id, decision, by)
            except Exception:
                logger.debug("feishu-approver: card patch failed", exc_info=True)
        return ok

    async def _send_card(self, approval: dict[str, Any]) -> None:
        from integrations.feishu_approver.feishu_card import build_prompt_card

        card = build_prompt_card(
            approval,
            timeout_seconds=self.settings.timeout_seconds,
            timeout_action=self.settings.timeout_action,
        )
        receive_id_type = "chat_id" if self.settings.chat_ids else "open_id"
        targets = self.settings.chat_ids or self.settings.open_ids
        if not targets:
            raise RuntimeError("no Feishu recipients configured")
        body = {
            "receive_id": targets[0],
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        message_id = await asyncio.to_thread(
            self._create_message, receive_id_type, body
        )
        with self._pending_lock:
            entry = self._pending.get(str(approval.get("id") or ""))
            if entry is not None:
                entry["messageId"] = message_id
        logger.info(
            "feishu-approver: prompt card sent approval=%s message=%s",
            approval.get("id"),
            message_id,
        )

    def _create_message(self, receive_id_type: str, body: dict[str, Any]) -> str:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        if self._lark_client is None:
            raise RuntimeError("Feishu client not started")

        request_body = (
            CreateMessageRequestBody.builder()
            .receive_id(body["receive_id"])
            .msg_type(body["msg_type"])
            .content(body["content"])
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(request_body)
            .build()
        )
        response = self._lark_client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu message create failed: {response.code} {response.msg}"
            )
        return str(getattr(response.data, "message_id", "") or "")

    async def _patch_card(
        self, message_id: str, approval_id: str, decision: str, by: str
    ) -> None:
        """Replace the prompt card with a resolved card (cosmetic, best-effort)."""
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        from integrations.feishu_approver.feishu_card import build_resolved_card

        if not message_id:
            return
        card = build_resolved_card(approval_id, decision=decision, by=by)
        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = await asyncio.to_thread(
            self._lark_client.im.v1.message.patch, request
        )
        if not response.success():
            logger.debug(
                "feishu-approver: patch failed for %s: %s %s",
                approval_id,
                response.code,
                response.msg,
            )

    # ------------------------------------------------------------- feishu

    def _start_feishu(self) -> None:
        """Start the Feishu long connection and build the sync API client."""
        import lark_oapi as lark

        # Warm the sync IM API now: its first import costs seconds, and the
        # first approval prompt must not pay that latency while a tool waits.
        import lark_oapi.api.im.v1
        import lark_oapi.ws.client as lark_ws_client
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

        # lark's ws client pins a module-level event loop at import time. We
        # import it from inside the bridge's running loop, so without this it
        # would capture *our* loop and the ws thread would die with
        # "This event loop is already running". Give it a loop of its own.
        lark_ws_client.loop = asyncio.new_event_loop()

        self._lark_client = (
            lark.Client.builder()
            .app_id(self.settings.app_id)
            .app_secret(self.settings.app_secret)
            .build()
        )
        handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_card_action_trigger(self._on_card_action)
            .build()
        )
        self._ws = lark.ws.Client(
            self.settings.app_id,
            self.settings.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        thread = threading.Thread(
            target=self._ws.start, name="feishu-approver-ws", daemon=True
        )
        thread.start()
        logger.info("feishu-approver: Feishu long connection starting")

    def _on_card_action(self, event: Any) -> Any:
        """Runs on the Feishu callback thread; returns a toast within 3s."""
        data = getattr(event, "event", event)
        operator = getattr(data, "operator", None)
        action = getattr(data, "action", None)
        open_id = getattr(operator, "open_id", None) or ""
        value = getattr(action, "value", None) or {}
        approval_id = str(value.get("approval_id") or "")
        action_name = str(value.get("action") or "")

        if open_id not in self.settings.open_ids:
            return self._toast("error", "Not authorized to approve.")
        decision = _ACTIONS.get(action_name)
        if decision is None or not approval_id:
            return self._toast("error", "Unknown action.")

        entry = self._take_pending(approval_id)
        if entry is None:
            return self._toast("warning", "Already resolved or unknown.")
        message_id = str(entry.get("messageId") or "")
        logger.info(
            "feishu-approver: card click approval=%s action=%s by=%s",
            approval_id,
            action_name,
            open_id,
        )

        self._schedule(
            self._finish(
                approval_id,
                decision,
                f"feishu:{action_name}",
                by=open_id,
                message_id=message_id,
            )
        )
        return self._toast("success", f"{_DECISION_LABELS[decision]}.")

    @staticmethod
    def _toast(toast_type: str, content: str) -> Any:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        # construct.parse() requires a plain dict for object-typed fields; it
        # re-checks isinstance(dict) and raises UnmarshalException otherwise.
        return P2CardActionTriggerResponse(
            {"toast": {"type": toast_type, "content": content}}
        )
