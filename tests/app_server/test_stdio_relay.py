"""The Desktop host reaches the shared service through this stdio relay."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time

import app_server.protocol.timeouts as timeouts
from app_server.stdio_relay import serve_relay
from tests.app_server.support import control_server
from tests.app_server.test_native_client import INITIALIZE, publish


class Desktop:
    """The host's end of the relay's stdin and stdout."""

    def __init__(self, files) -> None:
        stdin_read, stdin_write = os.pipe()
        stdout_read, stdout_write = os.pipe()
        self._requests = os.fdopen(stdin_write, "wb", buffering=0)
        self._responses: queue.Queue[dict] = queue.Queue()
        source = os.fdopen(stdin_read, "rb", buffering=0)
        sink = os.fdopen(stdout_write, "wb")
        self.relay = threading.Thread(
            target=serve_relay, args=(files, source, sink), daemon=True
        )
        self.relay.start()
        threading.Thread(
            target=self._read, args=(os.fdopen(stdout_read, "rb"),), daemon=True
        ).start()

    def _read(self, stdout) -> None:
        for line in stdout:
            self._responses.put(json.loads(line))

    def send(self, request_id: int, method: str, params: dict | None = None) -> None:
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method}
        self._requests.write(
            (json.dumps({**frame, "params": params or {}}) + "\n").encode()
        )

    def response(self, timeout: float = 10.0) -> dict:
        return self._responses.get(timeout=timeout)

    def close(self) -> None:
        self._requests.close()
        self.relay.join(timeout=5)


def _slow_list(monkeypatch, service, seconds: float) -> None:
    original = service.list

    def slow(*args, **kwargs):
        time.sleep(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "list", slow)


def test_the_relay_waits_as_long_as_the_method_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(timeouts, "DEFAULT_TIMEOUT", 0.3)
    monkeypatch.setitem(timeouts.SLOW_METHOD_TIMEOUTS, "project/list", 5.0)

    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            _slow_list(monkeypatch, control.host.application.threads, 1.0)
            _slow_list(monkeypatch, control.host.application.projects, 1.0)
            desktop = await asyncio.to_thread(Desktop, files)
            try:
                desktop.send(1, "initialize", INITIALIZE)
                await asyncio.to_thread(desktop.response)
                desktop.send(2, "thread/list", {"limit": 5})
                timed_out = await asyncio.to_thread(desktop.response)
                desktop.send(3, "project/list")
                answered = await asyncio.to_thread(desktop.response)

                assert timed_out["id"] == 2
                assert "did not answer within 0.3s" in timed_out["error"]["message"]
                assert answered["id"] == 3
                assert "projects" in answered["result"]
            finally:
                await asyncio.to_thread(desktop.close)
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_the_service_tells_clients_how_long_each_method_may_take(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            desktop = await asyncio.to_thread(Desktop, files)
            try:
                desktop.send(1, "initialize", INITIALIZE)
                info = (await asyncio.to_thread(desktop.response))["result"]
            finally:
                await asyncio.to_thread(desktop.close)
                files.clear()
                lease.close()
        budgets = info["capabilities"]["requestTimeouts"]
        assert budgets["defaultSeconds"] == timeouts.DEFAULT_TIMEOUT
        assert budgets["methodSeconds"]["thread/context/compact"] == 600.0

    asyncio.run(scenario())
