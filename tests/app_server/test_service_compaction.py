"""`/compact` through the shared service: same loop as Turns, readable refusals."""

from __future__ import annotations

import asyncio
import time

import pytest

from app_server.blocking_client import RemoteApplicationError
from core.events import AgentMessage, Event, TaskComplete, TurnStarted
from tests.app_server.support import control_server
from tests.app_server.test_native_client import publish
from tests.app_server.test_service_tui_state import _client


class _LoopRecordingFactory:
    """Records which event loop runs a Turn and which runs `/compact`."""

    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.loops: dict[str, asyncio.AbstractEventLoop] = {}

    def create(self, *, workspace, model, approval_callback):
        del workspace, model, approval_callback
        owner = self

        class Session:
            history: list[dict] = []

            def load_history(self, history):
                self.history = list(history)

            async def run_stream(self, _op):
                owner.loops["turn"] = asyncio.get_running_loop()
                yield Event("start", TurnStarted())
                yield Event("message", AgentMessage("done"))
                yield Event("done", TaskComplete("done", "completed"))

            async def compact(self):
                owner.loops["compact"] = asyncio.get_running_loop()
                if owner.refusal is not None:
                    raise RuntimeError(owner.refusal)
                return {
                    "replaced_messages": 2,
                    "messages_before": 4,
                    "messages_after": 2,
                    "chars_before": 100,
                    "chars_after": 40,
                }

            async def aclose(self):
                pass

        return Session()


def _compact_after_one_turn(tmp_path, factory):
    async def scenario():
        async with control_server(tmp_path, session_factory=factory) as (control, _):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            client = None
            try:
                client = await asyncio.to_thread(_client, files, workspace)
                await asyncio.to_thread(client.send, "make the runtime resident")
                turns = control.host.application.turns
                deadline = time.monotonic() + 5
                while turns.active_for_thread(client.thread.id) is not None:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.02)
                return await client.compact_context()
            finally:
                if client is not None:
                    await client.close()
                files.clear()
                lease.close()

    return asyncio.run(scenario())


def test_compaction_runs_on_the_turn_loop(tmp_path):
    factory = _LoopRecordingFactory()

    report = _compact_after_one_turn(tmp_path, factory)

    assert report["messages_after"] == 2
    # The resident agent's provider connections belong to the Turn loop.
    assert factory.loops["compact"] is factory.loops["turn"]


def test_compaction_refusal_reaches_the_client_as_its_reason(tmp_path):
    factory = _LoopRecordingFactory(refusal="No compactable history yet.")

    with pytest.raises(RemoteApplicationError) as raised:
        _compact_after_one_turn(tmp_path, factory)

    assert "No compactable history yet." in str(raised.value)
    assert raised.value.code != "INTERNAL_ERROR"
