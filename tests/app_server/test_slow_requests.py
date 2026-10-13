"""A request that waits on a model or a process must not hold up its connection.

The service used to answer one connection's requests strictly in turn, so a
/compact or an MCP test that took minutes stopped everything else that window
sent, and its live events, until it finished.
"""

from __future__ import annotations

import asyncio
import threading
import time

from app_server.native_client import NativeRpcClient
from core.domain import TrustState
from tests.app_server.support import control_server
from tests.app_server.test_native_client import INITIALIZE, publish


def test_a_slow_request_does_not_hold_up_its_connection(tmp_path, monkeypatch):
    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            application = control.host.application
            released = threading.Event()

            def slow_test(*args, **kwargs):
                released.wait(10)
                return {"ok": True}

            monkeypatch.setattr(application.llm, "test", slow_test)
            notifications: list[dict] = []
            client = NativeRpcClient(files, notify=notifications.append)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            project = application.projects.add(
                str(workspace), trust_state=TrustState.TRUSTED
            )
            try:
                await client.connect(INITIALIZE)
                slow = asyncio.create_task(
                    client.request("provider/test", {"connectionId": "deepseek"})
                )
                await asyncio.sleep(0.3)

                started = time.monotonic()
                listed = await client.request("thread/list", {"projectId": project.id})
                answered_in = time.monotonic() - started
                await asyncio.to_thread(
                    application.threads.start, project.id, title="Meanwhile"
                )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not any(
                    note.get("method") == "thread.updated" for note in notifications
                ):
                    await asyncio.sleep(0.05)

                assert listed == {"threads": []}
                assert answered_in < 2.0
                assert any(
                    note.get("method") == "thread.updated" for note in notifications
                )
                assert not slow.done()
                released.set()
                assert await slow == {"ok": True}
            finally:
                released.set()
                await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())
