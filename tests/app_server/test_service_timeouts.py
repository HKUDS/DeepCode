"""A slow service is reported once, as slow — never retried as a lost link."""

from __future__ import annotations

import asyncio
import time

import pytest

import app_server.native_client as native_client
from app_server.blocking_client import BlockingServiceClient, RemoteApplicationError
from tests.app_server.support import control_server
from tests.app_server.test_native_client import publish


def test_slow_read_is_asked_once_and_keeps_the_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(native_client, "_DEFAULT_TIMEOUT", 0.2)

    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            calls = []
            original = control.host.application.threads.list

            def slow(*args, **kwargs):
                calls.append(1)
                time.sleep(0.6)
                return original(*args, **kwargs)

            monkeypatch.setattr(control.host.application.threads, "list", slow)
            client = await asyncio.to_thread(BlockingServiceClient, files)
            try:
                started = time.monotonic()
                with pytest.raises(RemoteApplicationError) as raised:
                    await asyncio.to_thread(client.call, "thread/list", {"limit": 5})
                elapsed = time.monotonic() - started
                assert raised.value.timed_out is True
                assert "did not answer within 0.2s" in str(raised.value)
                assert calls == [1]
                # The socket was fine; nothing reconnected.
                assert client.generation == 0
                assert elapsed < 1.0
            finally:
                await asyncio.to_thread(client.close)
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_requests_that_wait_on_a_model_or_process_get_their_own_budget():
    assert native_client.request_timeout("thread/list") == 25.0
    assert native_client.request_timeout("provider/test") == 120.0
    assert native_client.request_timeout("thread/context/compact") == 600.0
    # An MCP server may be configured to take up to 300s to start.
    assert native_client.request_timeout("mcp/probe") > 300.0
