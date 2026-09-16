from __future__ import annotations

import io
import json
import queue
import threading
import time
from typing import Any

import pytest

from app_server.host import ServiceHost
from app_server.server import AppServer, serve_stdio
from core.application import DeepCodeApplication
from core.domain import TrustState
from tests.app_server.support import PausedFactory


def request(request_id: int, method: str, **params: Any) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


class Client:
    def __init__(self, host: ServiceHost, name: str) -> None:
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.peer = host.connect(self.send)
        self.peer.receive(
            request(
                1,
                "initialize",
                protocolVersion="1.0",
                clientInfo={"name": name, "version": "1.0"},
            )
        )
        assert self.result(1)["clientInfo"]["name"] == name

    def send(self, encoded: bytes) -> None:
        self.messages.put(json.loads(encoded))

    def result(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]

    def notification(self, method: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("method") == method:
                return message["params"]


@pytest.mark.parametrize("disconnect", ["eof", "shutdown"])
def test_disconnected_client_does_not_cancel_work_or_close_other_clients(
    tmp_path, disconnect
):
    factory = PausedFactory()
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3", session_factory=factory)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = app.threads.start(project.id, title="Shared host")

    with ServiceHost(app) as host:
        first = Client(host, "first")
        second = Client(host, "second")
        first.peer.receive(
            request(
                2,
                "turn/start",
                threadId=thread.id,
                prompt="run",
                messageId="shared-host-task",
            )
        )
        turn_id = first.result(2)["turn"]["id"]
        assert factory.started.wait(3)

        if disconnect == "eof":
            assert serve_stdio(first.peer, io.BytesIO()) == 0
        else:
            first.peer.receive(request(3, "shutdown"))
            assert first.result(3) == {"accepted": True}
        assert first.peer.closed
        assert not second.peer.closed

        second.peer.receive(request(2, "turn/read", turnId=turn_id))
        assert second.result(2)["turn"]["status"] == "running"
        second.peer.close()
        factory.release.set()
        assert factory.finished.wait(3)

        reconnected = Client(host, "reconnected")
        deadline = time.monotonic() + 3
        while True:
            reconnected.peer.receive(request(2, "turn/read", turnId=turn_id))
            state = reconnected.result(2)["turn"]["status"]
            if state == "completed":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert (workspace / "completed.txt").read_text() == "completed after disconnect"
        reconnected.peer.receive(request(3, "event/replay", threadId=thread.id))
        events = reconnected.result(3)["events"]
        assert any(event["type"] == "turn.completed" for event in events)
    assert reconnected.peer.closed


def test_one_config_watcher_is_shared_and_survives_client_disconnect(
    tmp_path, monkeypatch
):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    watchers = []

    class Watcher:
        def __init__(self, store, callback):
            self.callback = callback
            self.stopped = False
            watchers.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setattr("app_server.host.ConfigFileWatcher", Watcher)
    with ServiceHost(app) as host:
        first = Client(host, "first")
        second = Client(host, "second")
        assert len(watchers) == 1
        watchers[0].callback("revision-1")
        assert first.notification("settings.changed")["configRevision"] == "revision-1"
        assert second.notification("settings.changed")["configRevision"] == "revision-1"
        first.peer.close()
        assert not watchers[0].stopped
        watchers[0].callback("revision-2")
        assert second.notification("settings.changed")["configRevision"] == "revision-2"
    assert watchers[0].stopped


def test_failed_writer_disconnects_only_its_peer(tmp_path):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    with ServiceHost(app) as host:
        working = Client(host, "working")

        def failed_write(_frame):
            raise BrokenPipeError("disconnected")

        broken = host.connect(failed_write)
        with pytest.raises(BrokenPipeError):
            broken.receive(
                request(
                    1,
                    "initialize",
                    protocolVersion="1.0",
                    clientInfo={"name": "broken", "version": "1.0"},
                )
            )
        assert broken.closed
        working.peer.receive(request(2, "project/list"))
        assert working.result(2)["projects"] == []


def test_slow_client_does_not_block_notifications_or_requests_for_other_clients(
    tmp_path,
):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    entered = threading.Event()
    release = threading.Event()
    delivered: queue.Queue[dict[str, Any]] = queue.Queue()

    def slow_writer(frame):
        message = json.loads(frame)
        if message.get("method") == "terminal.output" and not entered.is_set():
            entered.set()
            assert release.wait(5)
        delivered.put(message)

    with ServiceHost(app, notification_capacity=2) as host:
        slow = host.connect(slow_writer)
        slow.receive(
            request(
                1,
                "initialize",
                protocolVersion="1.0",
                clientInfo={"name": "slow", "version": "1.0"},
            )
        )
        working = Client(host, "working")
        try:
            app.terminals._publish("terminal.output", {"text": "first"})
            assert entered.wait(2)
            for index in range(5):
                app.terminals._publish("terminal.output", {"text": str(index)})
            working.peer.receive(request(2, "project/list"))
            assert working.result(2)["projects"] == []
        finally:
            release.set()
        deadline = time.monotonic() + 3
        while True:
            message = delivered.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("params", {}).get("code") == "NOTIFICATION_QUEUE_OVERFLOW":
                assert message["params"]["dropped"] == 3
                assert message["params"]["replayRequired"] is True
                break


def test_startup_failure_releases_partial_subscriptions_and_application(
    tmp_path, monkeypatch
):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    calls = []
    original_unsubscribe = app.terminals.unsubscribe
    original_close = app.close

    def unsubscribe(token):
        calls.append("unsubscribe")
        original_unsubscribe(token)

    def close():
        calls.append("close")
        original_close()

    def fail(_listener):
        raise RuntimeError("subscription failed")

    monkeypatch.setattr(app.terminals, "unsubscribe", unsubscribe)
    monkeypatch.setattr(app.skills, "subscribe_changes", fail)
    monkeypatch.setattr(app, "close", close)
    host = ServiceHost(app)
    with pytest.raises(RuntimeError, match="subscription failed"):
        host.start()
    host.close()
    assert calls == ["unsubscribe", "close"]


def test_host_close_can_retry_application_cleanup_failure(tmp_path, monkeypatch):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    original_close = app.close
    attempts = 0

    def close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("cleanup still pending")
        original_close()

    monkeypatch.setattr(app, "close", close)
    host = ServiceHost(app)
    client = Client(host, "client")
    with pytest.raises(RuntimeError, match="cleanup still pending"):
        host.close()
    assert client.peer.closed
    host.close()
    host.close()
    assert attempts == 2


def test_private_stdio_eof_still_closes_its_application(tmp_path, monkeypatch):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    original_close = app.close
    closed = threading.Event()

    def close():
        original_close()
        closed.set()

    monkeypatch.setattr(app, "close", close)
    assert AppServer(app).serve(io.BytesIO(), io.BytesIO()) == 0
    assert closed.is_set()
