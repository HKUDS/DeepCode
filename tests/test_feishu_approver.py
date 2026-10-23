"""Closed-loop test for the Feishu approval bridge with a mock RPC client.

Covers the full decision path without touching a real DeepCode service or a
real Feishu tenant:

* ``approval.requested`` push  -> prompt card sent
* allowlisted card click       -> ``approval/respond`` approved_session
* unlisted card click          -> ignored (no respond)
* unanswered prompt            -> ``timeout_action`` decision on timeout
                                 (default fail-closed ``denied``)
* card-send failure            -> fail-closed ``denied`` (no hang)
* startup sweep                -> missed pending approval re-prompted
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from integrations.feishu_approver.bridge import (
    DECISION_APPROVE_SESSION,
    DECISION_DENY,
    ApprovalBridge,
)
from integrations.feishu_approver.config import BridgeSettings

ME = "ou_me"
OTHER = "ou_stranger"


def make_bridge(
    *, timeout_seconds: int = 0, timeout_action: str = "deny"
) -> tuple[ApprovalBridge, FakeRpc, "FakeFeishu"]:
    """Build a bridge wired to a FakeRpc and a fake Feishu sender.

    The FakeRpc is created here (not inside ``factory``) so the test and the
    bridge share one instance: sweep fixtures and ``responded`` must be visible
    to both sides.
    """
    settings = BridgeSettings(
        app_id="cli_test",
        app_secret="secret",
        open_ids=(ME,),
        timeout_seconds=timeout_seconds,
        timeout_action=timeout_action,
        chat_ids=(),
        database_path=None,
    )
    rpc = FakeRpc(lambda _: None)

    def factory(files, notify):
        rpc.notify = notify
        return rpc

    bridge = ApprovalBridge(settings, client_factory=factory)
    fake = FakeFeishu()
    # Replace the three lark-backed methods with fakes that mirror their
    # contracts but never import or construct lark SDK types, so these tests
    # run in a CI env that does not install the optional ``lark_oapi`` extra.
    bridge._create_message = fake.create  # type: ignore[method-assign]
    bridge._patch_card = fake.patch  # type: ignore[method-assign]
    bridge._toast = fake.toast  # type: ignore[method-assign]
    # Bypass the real Feishu long connection; tests drive clicks directly.
    bridge._start_feishu = lambda: None  # type: ignore[method-assign]
    return bridge, rpc, fake


def approval_view(approval_id: str, *, status: str = "pending") -> dict:
    return {
        "id": approval_id,
        "threadId": "thr_1",
        "turnId": "turn_1",
        "status": status,
        "request": {
            # Real core payloads are camelCase (see ApprovalService.request);
            # the old snake_case fixture masked the card's toolName bug.
            "toolName": "bash",
            "arguments": {"command": "ls"},
            "reason": "test",
        },
    }


def notify_frame(approval: dict) -> dict:
    return {
        "method": "approval.requested",
        "params": {"threadId": approval["threadId"], "payload": {"approval": approval}},
    }


def card_event(approval_id: str, *, open_id: str = ME, action: str = "approve_session"):
    """Fabricate the lark P2CardActionTrigger shape the handler reads."""
    return SimpleNamespace(
        event=SimpleNamespace(
            operator=SimpleNamespace(open_id=open_id),
            action=SimpleNamespace(
                value={"approval_id": approval_id, "action": action}
            ),
        )
    )


class FakeRpc:
    """Records requests and lets tests drive the notify callback."""

    def __init__(self, notify):
        self.notify = notify
        self.calls: list[tuple[str, dict]] = []
        self.responded: list[dict] = []
        self.fail_request = False
        self.sweep_threads: list[dict] = []
        self.sweep_turns: dict[str, list[dict]] = {}
        self.sweep_snapshots: dict[str, dict] = {}

    async def connect(self, initialize, *, start=False):
        return {"serviceInfo": {"instance": "test"}}

    async def request(self, method, params):
        self.calls.append((method, params))
        if self.fail_request:
            raise RuntimeError("fake rpc failure")
        if method == "approval/respond":
            self.responded.append(params)
            return {
                "approval": {"id": params["approvalId"], "status": params["decision"]}
            }
        if method == "thread/list":
            return {"threads": self.sweep_threads}
        if method == "turn/list":
            return {"turns": self.sweep_turns.get(params.get("threadId"), [])}
        if method == "turn/read":
            return self.sweep_snapshots.get(params.get("turnId"), {"approvals": []})
        return {}

    async def close(self):
        return None


class FakeFeishu:
    """Stands in for the three lark-backed methods the bridge calls.

    The real ``_create_message``/``_patch_card``/``_toast`` build ``lark_oapi``
    request/response objects; ``lark_oapi`` is an optional extra that a CI env
    may not have installed. These fakes replace those methods entirely (wired up
    in ``make_bridge``) so the tests exercise the bridge's decision logic without
    importing or constructing any lark SDK types.
    """

    def __init__(self):
        self.created: list[dict] = []
        self.patched: list[dict] = []
        self.toasts: list[dict] = []
        self.fail_create = False

    def create(self, receive_id_type: str, body: dict) -> str:
        """Mirrors ``ApprovalBridge._create_message``'s contract: return a
        message_id, or raise to trigger the bridge's fail-closed deny."""
        if self.fail_create:
            raise RuntimeError("fake card send failure")
        self.created.append(body)
        return f"om_{len(self.created)}"

    async def patch(
        self, message_id: str, approval_id: str, decision: str, by: str
    ) -> None:
        """Mirrors ``ApprovalBridge._patch_card``'s contract: best-effort, no raise."""
        self.patched.append(
            {
                "message_id": message_id,
                "approval_id": approval_id,
                "decision": decision,
                "by": by,
            }
        )

    def toast(self, toast_type: str, content: str) -> SimpleNamespace:
        """Mirrors ``ApprovalBridge._toast``'s shape: a response object with a
        ``.toast`` accessor carrying ``type`` and ``content``."""
        self.toasts.append({"type": toast_type, "content": content})
        return SimpleNamespace(toast=SimpleNamespace(type=toast_type, content=content))


async def settle(bridge: ApprovalBridge, *, rounds: int = 50):
    """Let scheduled coroutines and to_thread card sends finish."""
    for _ in range(rounds):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_push_then_click_approves():
    bridge, rpc, fake = make_bridge()
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    view = approval_view("apr_1")
    bridge._on_notify(notify_frame(view))
    await settle(bridge)

    assert "apr_1" in bridge._pending
    assert len(fake.created) == 1

    toast = bridge._on_card_action(card_event("apr_1", action="approve_session"))
    await settle(bridge)

    assert rpc.responded == [
        {
            "approvalId": "apr_1",
            "decision": DECISION_APPROVE_SESSION,
            "message": "feishu:approve_session",
        }
    ]
    assert "apr_1" not in bridge._pending
    assert toast.toast.type == "success"
    # The prompt card was patched to a resolved card.
    assert len(fake.patched) == 1


@pytest.mark.asyncio
async def test_unlisted_click_is_ignored():
    bridge, rpc, _ = make_bridge()
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    bridge._on_notify(notify_frame(approval_view("apr_2")))
    await settle(bridge)

    toast = bridge._on_card_action(card_event("apr_2", open_id=OTHER))
    await settle(bridge)

    assert rpc.responded == []
    assert "apr_2" in bridge._pending  # still awaiting an authorized decision
    assert toast.toast.type == "error"


@pytest.mark.asyncio
async def test_double_click_second_is_ignored():
    bridge, rpc, _ = make_bridge()
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    bridge._on_notify(notify_frame(approval_view("apr_3")))
    await settle(bridge)

    first = bridge._on_card_action(card_event("apr_3", action="approve_once"))
    second = bridge._on_card_action(card_event("apr_3", action="deny"))
    await settle(bridge)

    assert first.toast.type == "success"
    assert second.toast.type == "warning"
    decisions = [c["decision"] for c in rpc.responded]
    assert decisions == ["approved_once"]


@pytest.mark.asyncio
async def test_timeout_denies():
    bridge, rpc, _ = make_bridge(timeout_seconds=1)
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    bridge._on_notify(notify_frame(approval_view("apr_4")))
    await settle(bridge)
    assert "apr_4" in bridge._pending

    # Let the 1s timer fire.
    await asyncio.sleep(1.2)
    await settle(bridge)

    assert rpc.responded == [
        {"approvalId": "apr_4", "decision": DECISION_DENY, "message": "timeout"}
    ]
    assert "apr_4" not in bridge._pending


@pytest.mark.asyncio
async def test_timeout_approves_session_when_configured():
    """timeout_action=approve_session auto-allows instead of denying."""
    bridge, rpc, _ = make_bridge(timeout_seconds=1, timeout_action="approve_session")
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    bridge._on_notify(notify_frame(approval_view("apr_6")))
    await settle(bridge)
    assert "apr_6" in bridge._pending

    # Let the 1s timer fire.
    await asyncio.sleep(1.2)
    await settle(bridge)

    assert rpc.responded == [
        {
            "approvalId": "apr_6",
            "decision": DECISION_APPROVE_SESSION,
            "message": "timeout",
        }
    ]
    assert "apr_6" not in bridge._pending


@pytest.mark.asyncio
async def test_no_timeout_when_click_wins_race():
    bridge, rpc, _ = make_bridge(timeout_seconds=1)
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    bridge._on_notify(notify_frame(approval_view("apr_5")))
    await settle(bridge)
    bridge._on_card_action(card_event("apr_5", action="approve_once"))
    await settle(bridge)

    # Wait past the timeout window; the cancelled timer must not deny.
    await asyncio.sleep(1.2)
    await settle(bridge)

    assert [c["decision"] for c in rpc.responded] == ["approved_once"]


@pytest.mark.asyncio
async def test_card_send_failure_denies():
    bridge, rpc, fake = make_bridge()
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()
    fake.fail_create = True

    bridge._on_notify(notify_frame(approval_view("apr_6")))
    await settle(bridge)

    assert rpc.responded == [
        {
            "approvalId": "apr_6",
            "decision": DECISION_DENY,
            "message": "card delivery failed",
        }
    ]
    assert "apr_6" not in bridge._pending


@pytest.mark.asyncio
async def test_sweep_recovers_missed_pending():
    bridge, rpc, fake = make_bridge()
    bridge._loop = asyncio.get_running_loop()
    await bridge._connect_rpc()

    missed = approval_view("apr_7")
    rpc.sweep_threads = [{"id": "thr_1", "status": "waiting"}]
    rpc.sweep_turns = {"thr_1": [{"id": "turn_1"}]}
    rpc.sweep_snapshots = {"turn_1": {"approvals": [missed]}}

    await bridge._sweep_pending()
    await settle(bridge)

    assert "apr_7" in bridge._pending
    assert len(fake.created) == 1
