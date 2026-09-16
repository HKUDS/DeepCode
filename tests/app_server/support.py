"""Shared service harness and controllable Agent session for transport tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from pathlib import Path
import threading

from aiohttp.test_utils import TestClient, TestServer

from app_server.host import ServiceHost
from app_server.service import ControlServer
from app_server.service_state import ServiceRecord
from core.application.application import DeepCodeApplication
from core.events import AgentMessage, Event, TaskComplete, TurnStarted


@asynccontextmanager
async def control_server(
    tmp_path, *, database_name="state.sqlite3", **application_options
):
    application = await asyncio.to_thread(
        DeepCodeApplication.open, tmp_path / database_name, **application_options
    )
    host = ServiceHost(application)
    host.start()
    control = ControlServer(
        host,
        ServiceRecord("a" * 32, str(application.database.path), os.getpid(), 1),
        "b" * 64,
    )
    client = TestClient(TestServer(control.application()))
    try:
        await client.start_server()
        control.record = replace(control.record, port=client.server.port)
        yield control, client
    finally:
        control.interrupt.set()
        await client.close()
        await asyncio.to_thread(host.close)


def auth(control):
    return {
        "Authorization": "Bearer " + "b" * 64,
        "X-DeepCode-Instance": control.record.instance_id,
    }


def body(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


class PausedFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def create(self, *, workspace, model, approval_callback):
        owner = self

        class Session:
            def load_history(self, _history):
                pass

            async def run_stream(self, _op):
                yield Event("start", TurnStarted())
                owner.started.set()
                while not owner.release.is_set():
                    await asyncio.sleep(0.01)
                Path(workspace, "completed.txt").write_text(
                    "completed after disconnect"
                )
                yield Event("message", AgentMessage("finished"))
                yield Event("done", TaskComplete("finished", "completed"))
                owner.finished.set()

            async def aclose(self):
                pass

        return Session()
