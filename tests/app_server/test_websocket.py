from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import threading

from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType, web
import pytest

from app_server.browser_auth import BrowserAuth
from app_server.service_client import ServiceClient, ServiceUnavailable
from app_server.service_state import ServiceFiles
from app_server.websocket import FrameQueue
from cli.service_cli import start_service, stop_service
from core.domain import TrustState
from core.persistence.event_repository import EventRepository
from tests.app_server.support import PausedFactory, auth, body, control_server
from tests.test_turn_steering import _SteeringFactory


def test_replay_cutoff_and_reconnect_cover_concurrent_live_overflow(
    tmp_path, monkeypatch
):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Replay boundary")
            app.broker.default_capacity = 3
            ws = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(ws)

            def append(count):
                with app.database.transaction() as connection:
                    repository = EventRepository(connection)
                    events = [
                        repository.append(
                            thread_id=thread.id,
                            type="test.delta",
                            payload={"delta": str(index)},
                        )
                        for index in range(count)
                    ]
                for event in reversed(events):
                    app.broker.publish(event)

            append(6)
            captured = app.events.head(thread.id)
            original = app.events.replay_page
            entered, release = threading.Event(), threading.Event()

            def paused(*args, **kwargs):
                page = original(*args, **kwargs)
                entered.set()
                assert release.wait(5)
                return page

            monkeypatch.setattr(app.events, "replay_page", paused)
            await ws.send_json(
                body("event/replay", {"threadId": thread.id, "limit": 2})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                append(30)
            finally:
                release.set()
            page = (await reply(ws))["result"]
            assert page["headSequence"] == captured
            replayed = page["events"][:]
            async with asyncio.timeout(3):
                while True:
                    notification = await ws.receive_json()
                    if notification.get("method") == "server.warning":
                        assert notification["params"]["code"] == "EVENT_QUEUE_OVERFLOW"
                        assert notification["params"]["replayRequired"] is True
                        break
            monkeypatch.setattr(app.events, "replay_page", original)
            while page["hasMore"]:
                page = await rpc(
                    ws,
                    "event/replay",
                    {
                        "threadId": thread.id,
                        "after": page["nextAfter"],
                        "limit": 2,
                        "through": captured,
                    },
                )
                assert page["headSequence"] == captured
                replayed.extend(page["events"])
            assert [event["sequence"] for event in replayed] == list(
                range(1, captured + 1)
            )
            await ws.close()
            append(5)  # No subscribed client; these must come from durable replay.
            reconnected = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(reconnected)
            page = await rpc(
                reconnected, "event/replay", {"threadId": thread.id, "after": captured}
            )
            replayed.extend(page["events"])
            assert [event["sequence"] for event in replayed] == list(
                range(1, app.events.head(thread.id) + 1)
            )
            assert page["hasMore"] is False

    asyncio.run(asyncio.wait_for(scenario(), 20))


async def reply(ws, request_id=1):
    async with asyncio.timeout(5):
        while True:
            message = await ws.receive_json()
            if message.get("id") == request_id:
                return message


async def closed_message(ws):
    async with asyncio.timeout(5):
        while (message := await ws.receive()).type == WSMsgType.TEXT:
            pass
        assert message.type == WSMsgType.CLOSE
        return message


async def rpc(ws, method, params=None):
    await ws.send_json(body(method, params))
    result = await reply(ws)
    assert "error" not in result, result
    return result["result"]


async def initialize(ws, name="test", version="1.0"):
    return await rpc(
        ws,
        "initialize",
        {
            "protocolVersion": version,
            "clientInfo": {"name": name, "version": "1", "surface": "web"},
        },
    )


async def browser_headers(control, client):
    response = await client.post(
        "/control/rpc", headers=auth(control), json=body("auth/issue")
    )
    ticket = (await response.json())["result"]["ticket"]
    response = await client.post(
        "/auth/exchange",
        headers={"Origin": control.record.url},
        json={"ticket": ticket},
    )
    assert response.status == 200
    cookie = response.cookies[control.browser_auth.cookie_name]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert response.headers["Cache-Control"] == "no-store"
    assert cookie.value not in await response.text()
    # Tests explicitly select sessions, instead of the last Set-Cookie winning.
    client.session.cookie_jar.clear()
    return {
        "Origin": control.record.url,
        "Cookie": f"{cookie.key}={cookie.value}",
    }, ticket


def test_browser_auth_origin_replay_logout_and_management_separation(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            for headers, status in [
                ({}, 401),
                ({"Origin": control.record.url}, 401),
                ({**auth(control), "Origin": "https://evil.example"}, 403),
                ({**auth(control), "Host": "evil.example"}, 403),
                ({**auth(control), "Origin": "null"}, 403),
                ({**auth(control), "X-DeepCode-Instance": "wrong"}, 401),
            ]:
                with pytest.raises(WSServerHandshakeError) as failure:
                    await client.ws_connect("/api/rpc", headers=headers)
                assert failure.value.status == status
            headers, ticket = await browser_headers(control, client)
            assert (
                await client.post(
                    "/auth/exchange", headers=headers, json={"ticket": ticket}
                )
            ).status == 401
            assert (
                await client.post("/auth/exchange", json={"ticket": ticket})
            ).status == 403
            # Even an authorized browser cannot operate the management plane.
            assert (
                await client.post("/control/rpc", headers=headers, json=body("stop"))
            ).status == 403
            assert (
                await client.post(
                    "/control/rpc",
                    headers={"Cookie": headers["Cookie"]},
                    json=body("stop"),
                )
            ).status == 401
            with pytest.raises(WSServerHandshakeError) as failure:
                await client.ws_connect(
                    "/api/rpc", headers={"Cookie": headers["Cookie"]}
                )
            assert failure.value.status == 401
            first = await client.ws_connect("/api/rpc", headers=headers)
            sibling = await client.ws_connect("/api/rpc", headers=headers)
            other_headers, _ = await browser_headers(control, client)
            other = await client.ws_connect("/api/rpc", headers=other_headers)
            await initialize(first)
            await initialize(sibling)
            await initialize(other)
            response = await client.post("/auth/logout", headers=headers)
            assert response.status == 200
            assert (await first.receive()).type == WSMsgType.CLOSE
            assert (await sibling.receive()).type == WSMsgType.CLOSE
            assert await rpc(other, "project/list") == {"projects": []}
            with pytest.raises(WSServerHandshakeError):
                await client.ws_connect("/api/rpc", headers=headers)
            assert control.phase == "ready"

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_ticket_expiry_session_expiry_limits_and_restart():
    now = [100.0]
    sessions = BrowserAuth("first", clock=lambda: now[0])
    ticket = sessions.issue()["ticket"]
    now[0] += 60
    with pytest.raises(web.HTTPUnauthorized):
        sessions.exchange(ticket)
    session = sessions.exchange(sessions.issue()["ticket"])
    assert sessions.remaining(session) == sessions.SESSION_TTL
    assert BrowserAuth("second").remaining(session) == 0
    now[0] += sessions.SESSION_TTL
    assert sessions.remaining(session) == 0
    sessions.EXCHANGES_PER_MINUTE = 2
    for _ in range(2):
        with pytest.raises(web.HTTPUnauthorized):
            sessions.exchange("wrong")
    with pytest.raises(web.HTTPTooManyRequests):
        sessions.exchange("wrong")
    now[0] += 60
    sessions.CAPACITY = 1
    ticket = sessions.issue()["ticket"]
    with pytest.raises(web.HTTPTooManyRequests):
        sessions.issue()
    sessions.exchange(ticket)
    with pytest.raises(web.HTTPTooManyRequests):
        sessions.exchange(sessions.issue()["ticket"])


@pytest.mark.parametrize("disconnect", ["close", "shutdown", "logout"])
def test_network_disconnect_keeps_turn_running_and_other_peer_independent(
    tmp_path, disconnect
):
    async def scenario():
        factory = PausedFactory()
        async with control_server(tmp_path, session_factory=factory) as (
            control,
            client,
        ):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Network lifecycle")
            headers, _ = await browser_headers(control, client)
            first = await client.ws_connect("/api/rpc", headers=headers)
            second = await client.ws_connect("/api/rpc", headers=auth(control))
            await first.send_json(body("project/list"))
            assert (await reply(first))["error"]["data"]["code"] == "NOT_INITIALIZED"
            await first.send_json(
                body("initialize", {"protocolVersion": "99", "clientInfo": {}})
            )
            assert (await reply(first))["error"]["data"]["code"] == "PROTOCOL_MISMATCH"
            info = await initialize(first, "first")
            assert info["serviceInfo"]["instanceId"] == control.record.instance_id
            assert info["serviceInfo"]["schemaVersion"] == app.database.schema_version()
            assert info["serviceInfo"]["shutdownScope"] == "connection"
            assert "turn/start" in info["capabilities"]["methods"]
            assert (await initialize(second, "second"))["clientInfo"][
                "name"
            ] == "second"
            result = await rpc(
                first,
                "turn/start",
                {
                    "threadId": thread.id,
                    "prompt": "run",
                    "messageId": "network-turn",
                },
            )
            turn_id = result["turn"]["id"]
            try:
                assert await asyncio.to_thread(factory.started.wait, 3)
                if disconnect == "shutdown":
                    assert await rpc(first, "shutdown") == {"accepted": True}
                    await closed_message(first)
                elif disconnect == "logout":
                    assert (
                        await client.post("/auth/logout", headers=headers)
                    ).status == 200
                else:
                    await first.close()
                assert (await rpc(second, "turn/read", {"turnId": turn_id}))["turn"][
                    "status"
                ] == "running"
                await second.close()
                factory.release.set()
                assert await asyncio.to_thread(factory.finished.wait, 3)
                reconnected = await client.ws_connect("/api/rpc", headers=auth(control))
                await initialize(reconnected)
                async with asyncio.timeout(3):
                    while (await rpc(reconnected, "turn/read", {"turnId": turn_id}))[
                        "turn"
                    ]["status"] != "completed":
                        await asyncio.sleep(0.01)
                events = (
                    await rpc(reconnected, "event/replay", {"threadId": thread.id})
                )["events"]
                assert any(event["type"] == "turn.completed" for event in events)
                assert not any(
                    event["type"] == "thread.projection_conflict" for event in events
                )
                assert (
                    workspace / "completed.txt"
                ).read_text() == "completed after disconnect"
            finally:
                factory.release.set()

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_drain_waits_for_admitted_rpc_and_restores_service_on_timeout(
    tmp_path, monkeypatch
):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            entered = threading.Event()
            release = threading.Event()
            original = control.host.application.projects.list

            def slow(*args, **kwargs):
                entered.set()
                assert release.wait(5)
                return original(*args, **kwargs)

            monkeypatch.setattr(control.host.application.projects, "list", slow)
            slow_ws = await client.ws_connect(
                "/api/rpc", headers=auth(control), autoping=False
            )
            fast_ws = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(slow_ws)
            await initialize(fast_ws)
            try:
                await slow_ws.send_json(body("project/list"))
                assert await asyncio.to_thread(entered.wait, 2)
                await slow_ws.ping(b"still-connected")
                pong = await asyncio.wait_for(slow_ws.receive(), 2)
                assert pong.type == WSMsgType.PONG and pong.data == b"still-connected"
                # A blocked business call cannot consume management's executor.
                async with asyncio.timeout(2):
                    response = await client.post(
                        "/control/rpc", headers=auth(control), json=body("status")
                    )
                    assert (await response.json())["result"]["phase"] == "ready"
                    assert (await initialize(fast_ws))["protocolVersion"] == "1.0"
                response = await client.post(
                    "/control/rpc",
                    headers=auth(control),
                    json=body("drain", {"timeout": 0}),
                )
                assert "error" in await response.json()
                assert control.phase == "ready"
            finally:
                release.set()
            assert "result" in await reply(slow_ws)
            response = await client.post(
                "/control/rpc",
                headers=auth(control),
                json=body("drain", {"timeout": 1}),
            )
            assert (await response.json())["result"]["accepted"]
            await fast_ws.send_json(body("thread/start", {}))
            assert (await reply(fast_ws))["error"]["data"]["code"] == "SERVICE_DRAINING"
            await fast_ws.send_json(body("approval/respond", {}))
            # Reaches the existing handler's parameter checks while draining.
            assert (await reply(fast_ws))["error"]["data"]["code"] == "INVALID_REQUEST"
            await client.post(
                "/control/rpc", headers=auth(control), json=body("resume")
            )
            assert (await rpc(fast_ws, "project/list"))["projects"] == []

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_invalid_frames_and_connection_limit_do_not_break_other_clients(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            control.business.MAX_CONNECTIONS = 2
            # A failed HTTP upgrade must release its reserved slot.
            assert (await client.get("/api/rpc", headers=auth(control))).status == 400
            first = await client.ws_connect("/api/rpc", headers=auth(control))
            second = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(second)
            with pytest.raises(WSServerHandshakeError) as failure:
                await client.ws_connect("/api/rpc", headers=auth(control))
            assert failure.value.status == 503
            for raw in ["invalid JSON", "[" * 2000 + "]" * 2000]:
                await first.send_str(raw)
                assert (await reply(first, None))["error"]["code"] in {-32700, -32600}
            await first.send_bytes(b"no binary protocol")
            message = await first.receive()
            assert message.type == WSMsgType.CLOSE and message.data == 1003
            assert (await rpc(second, "project/list"))["projects"] == []

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_outbox_overflow_is_bounded_and_finish_preserves_replies():
    async def scenario():
        for options in [{"max_frames": 1}, {"max_bytes": 3}]:
            outbox = FrameQueue(**options)

            def overflow():
                outbox.send(b"one")
                with pytest.raises(BrokenPipeError):
                    outbox.send(b"two")

            await asyncio.to_thread(overflow)
            assert outbox.overflowed
            assert await outbox.receive() is None
        outbox = FrameQueue()
        await asyncio.to_thread(outbox.send, b"reply")
        outbox.finish()
        assert await outbox.receive() == b"reply"
        assert await outbox.receive() is None

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_session_expiry_closes_existing_socket_and_rejects_reconnect(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            control.browser_auth.SESSION_TTL = 1
            headers, _ = await browser_headers(control, client)
            browser = await client.ws_connect("/api/rpc", headers=headers)
            native = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(browser)
            await initialize(native)
            assert (await closed_message(browser)).data == 1008
            with pytest.raises(WSServerHandshakeError) as failure:
                await client.ws_connect("/api/rpc", headers=headers)
            assert failure.value.status == 401
            assert (await rpc(native, "project/list"))["projects"] == []

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_disconnect_during_admitted_mutation_does_not_cancel_the_write(
    tmp_path, monkeypatch
):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            entered, release = threading.Event(), threading.Event()
            projects = control.host.application.projects
            original = projects.add
            workspace = tmp_path / "workspace"
            workspace.mkdir()

            def delayed(*args, **kwargs):
                entered.set()
                assert release.wait(5)
                return original(*args, **kwargs)

            monkeypatch.setattr(projects, "add", delayed)
            ws = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(ws)
            await ws.send_json(body("project/add", {"path": str(workspace)}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                ws._response.close()  # Simulate a lost socket without a close handshake.
                response = await client.post(
                    "/control/rpc",
                    headers=auth(control),
                    json=body("stop", {"timeout": 0, "cancelRunning": True}),
                )
                assert "error" in await response.json()
                assert not control.stopped.is_set()
            finally:
                release.set()
            assert await control.business.wait_idle(3)
            reconnected = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(reconnected)
            result = await rpc(reconnected, "project/list")
            assert len(result["projects"]) == 1
            assert result["projects"][0]["canonicalPath"] == str(workspace.resolve())

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_slow_socket_overflows_without_blocking_other_clients(tmp_path, monkeypatch):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            slow = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(slow)
            server_socket = next(iter(control.business._connections))
            fast = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(fast)
            entered, release = asyncio.Event(), asyncio.Event()
            original = server_socket.send_str

            async def stalled(frame):
                entered.set()
                await release.wait()
                await original(frame)

            monkeypatch.setattr(server_socket, "send_str", stalled)
            publish = control.host.application.terminals._publish
            publish("terminal.output", {"terminalId": "test", "text": "first"})
            await asyncio.wait_for(entered.wait(), 2)
            try:
                # Feed several batches: a healthy connection can drain each one,
                # while the stalled writer accumulates output until it is evicted.
                async with asyncio.timeout(5):
                    while len(control.host._peers) != 1:
                        for index in range(80):
                            publish(
                                "terminal.output",
                                {"terminalId": "test", "text": str(index)},
                            )
                        assert (await rpc(fast, "project/list"))["projects"] == []
                        await asyncio.sleep(0.03)
            finally:
                release.set()
            assert (await closed_message(slow)).data == 1013
            assert (await rpc(fast, "project/list"))["projects"] == []
            control.host.max_message_bytes = 1024
            oversized = await client.ws_connect("/api/rpc", headers=auth(control))
            await oversized.send_str("x" * 1025)
            assert (await closed_message(oversized)).data == 1009
            assert (await rpc(fast, "project/list"))["projects"] == []
            assert control.phase == "ready"

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


@pytest.mark.parametrize("steer_state", [None, "pending", "accepted"])
def test_process_crash_interrupts_running_work_without_repeating_it(
    tmp_path, steer_state
):
    async def scenario():
        files = ServiceFiles(tmp_path / "state.sqlite3")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.app_server.crash_worker",
                str(files.database),
                *(["--pause-steer"] if steer_state == "pending" else []),
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(15):
                while True:
                    try:
                        await asyncio.to_thread(
                            ServiceClient(files).call, "status", timeout=1
                        )
                        break
                    except ServiceUnavailable:
                        assert process.poll() is None
                        await asyncio.sleep(0.05)
            record, token = files.read()
            assert record.pid == process.pid
            headers = {
                "Authorization": "Bearer " + token,
                "X-DeepCode-Instance": record.instance_id,
            }
            async with ClientSession(trust_env=False) as http:
                async with http.ws_connect(
                    record.url + "/api/rpc", headers=headers
                ) as ws:
                    await initialize(ws)
                    project = (
                        await rpc(
                            ws,
                            "project/add",
                            {"path": str(workspace), "trustState": "trusted"},
                        )
                    )["project"]
                    thread = (
                        await rpc(
                            ws,
                            "thread/start",
                            {"projectId": project["id"], "title": "Crash recovery"},
                        )
                    )["thread"]
                    turn = (
                        await rpc(
                            ws,
                            "turn/start",
                            {
                                "threadId": thread["id"],
                                "prompt": "record then wait",
                                "messageId": "crash-test",
                            },
                        )
                    )["turn"]
                    marker = workspace / "executions.txt"
                    async with asyncio.timeout(5):
                        while not marker.exists():
                            await asyncio.sleep(0.01)
                    assert (await rpc(ws, "turn/read", {"turnId": turn["id"]}))["turn"][
                        "status"
                    ] == "running"
                    steer_params = {
                        "threadId": thread["id"],
                        "expectedTurnId": turn["id"],
                        "prompt": "Keep the API stable",
                        "messageId": "crash-steer",
                    }
                    if steer_state == "pending":
                        await ws.send_json(body("turn/steer", steer_params))
                        async with asyncio.timeout(5):
                            while not (tmp_path / "steer-pending").exists():
                                await asyncio.sleep(0.01)
                        async with http.ws_connect(
                            record.url + "/api/rpc", headers=headers
                        ) as observer:
                            await initialize(observer)
                            receipt = await rpc(
                                observer,
                                "turn/input/read",
                                {"threadId": thread["id"], "messageId": "crash-steer"},
                            )
                            assert (
                                receipt["item"]["payload"]["deliveryState"] == "pending"
                            )
                    elif steer_state == "accepted":
                        assert (await rpc(ws, "turn/steer", steer_params))[
                            "deliveryState"
                        ] == "accepted"
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)
                restarted = await asyncio.to_thread(start_service, files, port=0)
                assert restarted["instanceId"] != record.instance_id
                record, token = files.read()
                headers = {
                    "Authorization": "Bearer " + token,
                    "X-DeepCode-Instance": record.instance_id,
                }
                async with http.ws_connect(
                    record.url + "/api/rpc", headers=headers
                ) as ws:
                    await initialize(ws)
                    recovered = (await rpc(ws, "turn/read", {"turnId": turn["id"]}))[
                        "turn"
                    ]
                    assert recovered["status"] == "interrupted"
                    assert recovered["stopReason"] == "worker_crashed"
                    if steer_state:
                        receipt = await rpc(
                            ws,
                            "turn/input/read",
                            {"threadId": thread["id"], "messageId": "crash-steer"},
                        )
                        assert receipt["item"]["payload"]["deliveryState"] == (
                            "unknown" if steer_state == "pending" else "accepted"
                        )
                        await ws.send_json(body("turn/steer", steer_params))
                        retry = await reply(ws)
                        if steer_state == "pending":
                            assert (
                                retry["error"]["data"]["code"]
                                == "INPUT_DELIVERY_UNCERTAIN"
                            )
                            assert retry["error"]["data"]["retryable"] is False
                        else:
                            assert retry["result"]["duplicate"] is True
                    events = (
                        await rpc(ws, "event/replay", {"threadId": thread["id"]})
                    )["events"]
                    assert any(event["type"] == "turn.recovered" for event in events)
                    assert not any(
                        event["type"] == "thread.projection_conflict"
                        for event in events
                    )
                    assert marker.read_text() == "started\n"
                    assert (
                        await asyncio.to_thread(ServiceClient(files).call, "status")
                    )["activeTurns"] == 0
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            if files.running():
                await asyncio.to_thread(
                    stop_service, files, timeout=2, cancel_running=True
                )

    asyncio.run(asyncio.wait_for(scenario(), timeout=35))


@pytest.mark.parametrize("method", ["turn/start", "turn/enqueue"])
def test_lost_submission_response_can_be_queried_and_retried_without_new_work(
    tmp_path, monkeypatch, method
):
    async def scenario():
        factory = PausedFactory()
        async with control_server(tmp_path, session_factory=factory) as (
            control,
            client,
        ):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Lost response")
            first = await client.ws_connect("/api/rpc", headers=auth(control))
            info = await initialize(first)
            retry = info["capabilities"]["requestRetry"]
            assert retry["default"] == "never"
            assert retry["keyedMethods"][method] == "messageId"
            assert retry["keyedMethods"]["turn/steer"] == "messageId"
            assert "turn/input/read" in retry["readMethods"]
            assert not {
                "terminal/write",
                "file/write",
                "settings/update",
            } & set(retry["keyedMethods"])
            server_socket = next(iter(control.business._connections))
            send = server_socket.send_str
            response_lost = asyncio.Event()

            async def lose_reply(frame):
                if json.loads(frame).get("id") == 77:
                    response_lost.set()
                    await server_socket.close()
                else:
                    await send(frame)

            monkeypatch.setattr(server_socket, "send_str", lose_reply)
            params = {"threadId": thread.id, "prompt": "run", "messageId": "lost-reply"}
            try:
                await first.send_json(
                    {"jsonrpc": "2.0", "id": 77, "method": method, "params": params}
                )
                await asyncio.wait_for(response_lost.wait(), 3)
                await first.close()
                second = await client.ws_connect("/api/rpc", headers=auth(control))
                await initialize(second)
                receipt = await rpc(
                    second,
                    "turn/input/read",
                    {"threadId": thread.id, "messageId": "lost-reply"},
                )
                turn_id = receipt["item"]["turnId"]
                for _ in range(5):
                    assert (await rpc(second, method, params))["turn"]["id"] == turn_id
                await second.send_json(
                    body(method, {**params, "model": "different-model"})
                )
                assert (await reply(second))["error"]["data"][
                    "code"
                ] == "DUPLICATE_MESSAGE_CONFLICT"
                factory.release.set()
                assert await asyncio.to_thread(factory.finished.wait, 3)
                async with asyncio.timeout(3):
                    while (await rpc(second, "turn/read", {"turnId": turn_id}))["turn"][
                        "status"
                    ] != "completed":
                        await asyncio.sleep(0.01)
                with app.database.read() as connection:
                    assert (
                        connection.execute(
                            "SELECT COUNT(*) FROM turns WHERE thread_id = ?",
                            (thread.id,),
                        ).fetchone()[0]
                        == 1
                    )
                assert (workspace / "completed.txt").exists()
                response = await client.post(
                    "/control/rpc",
                    headers=auth(control),
                    json=body("drain", {"timeout": 1}),
                )
                assert (await response.json())["result"]["accepted"]
                assert (
                    await rpc(
                        second,
                        "turn/input/read",
                        {"threadId": thread.id, "messageId": "lost-reply"},
                    )
                )["item"]["turnId"] == turn_id
            finally:
                factory.release.set()

    asyncio.run(asyncio.wait_for(scenario(), timeout=20))


def test_lost_steer_response_is_confirmed_and_injected_once(tmp_path, monkeypatch):
    async def scenario():
        factory = _SteeringFactory()
        async with control_server(tmp_path, session_factory=factory) as (
            control,
            client,
        ):
            app = control.host.application
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
            thread = app.threads.start(project.id, title="Lost Steer acknowledgement")
            first = await client.ws_connect("/api/rpc", headers=auth(control))
            await initialize(first)
            turn = (
                await rpc(
                    first,
                    "turn/start",
                    {
                        "threadId": thread.id,
                        "prompt": "Initial task",
                        "messageId": "initial",
                    },
                )
            )["turn"]
            assert await asyncio.to_thread(factory.started.wait, 3)
            server_socket = next(iter(control.business._connections))
            send = server_socket.send_str
            lost = asyncio.Event()

            async def lose_reply(frame):
                if json.loads(frame).get("id") == 77:
                    lost.set()
                    await server_socket.close()
                else:
                    await send(frame)

            monkeypatch.setattr(server_socket, "send_str", lose_reply)
            params = {
                "threadId": thread.id,
                "expectedTurnId": turn["id"],
                "prompt": "Keep the API stable",
                "messageId": "lost-steer",
            }
            try:
                await first.send_json({**body("turn/steer", params), "id": 77})
                await asyncio.wait_for(lost.wait(), 5)
                second = await client.ws_connect("/api/rpc", headers=auth(control))
                await initialize(second)
                receipt = await rpc(
                    second,
                    "turn/input/read",
                    {"threadId": thread.id, "messageId": "lost-steer"},
                )
                assert receipt["item"]["payload"]["deliveryState"] == "accepted"
                for _ in range(5):
                    duplicate = await rpc(second, "turn/steer", params)
                    assert duplicate["duplicate"] is True
                    assert duplicate["deliveryState"] == "accepted"
                    assert duplicate["turn"]["id"] == turn["id"]
                factory.release.set()
                async with asyncio.timeout(5):
                    while (await rpc(second, "turn/read", {"turnId": turn["id"]}))[
                        "turn"
                    ]["status"] != "completed":
                        await asyncio.sleep(0.01)
                assert factory.injected == ["Keep the API stable"]
                events = (await rpc(second, "event/replay", {"threadId": thread.id}))[
                    "events"
                ]
                assert sum(event["type"] == "turn.steered" for event in events) == 1
                assert not any(
                    event["type"] == "thread.projection_conflict" for event in events
                )
            finally:
                factory.release.set()

    asyncio.run(asyncio.wait_for(scenario(), 20))
