from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from core.application.application import DeepCodeApplication
from core.application.service_lifecycle import ServiceLifecycle
from core.domain import TrustState
from core.events import AgentMessage, Event, TaskComplete, TurnStarted


class BlockingFactory:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def create(self, *, workspace, model, approval_callback):
        owner = self

        class Session:
            def load_history(self, history):
                pass

            async def run_stream(self, op):
                yield Event("start", TurnStarted())
                owner.started.set()
                while not owner.release.is_set():
                    await asyncio.sleep(0.01)
                yield Event("message", AgentMessage("done"))
                yield Event("done", TaskComplete("done", "completed"))

            async def aclose(self):
                pass

        return Session()


def test_drain_timeout_restores_scheduler_and_preserves_running_turn(tmp_path: Path):
    factory = BlockingFactory()
    app = DeepCodeApplication.open(
        tmp_path / "state.sqlite3",
        session_factory=factory,
        run_automation_scheduler=True,
    )
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
        thread = app.threads.start(project.id, title="Drain compatibility")
        turn = app.turns.start(thread.id, prompt="run", message_id="drain-test").turn
        assert factory.started.wait(3)
        lifecycle = ServiceLifecycle(app)
        assert lifecycle.drain(0, threading.Event()) is False
        assert app.automation_scheduler.active
        assert lifecycle.activity()["activeTurns"] == 1
        assert app.turns.read(turn.id).turn.status.value == "running"
        factory.release.set()
        assert lifecycle.drain(3, threading.Event()) is True
        assert app.turns.read(turn.id).turn.status.value == "completed"
        assert not app.automation_scheduler.active
    finally:
        factory.release.set()
        app.close()


def test_drain_interrupt_restores_an_idle_service(tmp_path):
    app = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", run_automation_scheduler=True
    )
    try:
        interrupt = threading.Event()
        interrupt.set()
        assert ServiceLifecycle(app).drain(10, interrupt) is False
        assert app.automation_scheduler.active
        # Admission remains usable, rather than permanently quiesced.
        assert app.execution_coordinator.dispatch_once() == ()
    finally:
        app.close()


def test_completed_supervisor_drain_can_restore_scheduler_and_admission(tmp_path):
    app = DeepCodeApplication.open(
        tmp_path / "state.sqlite3", run_automation_scheduler=True
    )
    try:
        lifecycle = ServiceLifecycle(app)
        assert lifecycle.drain(0, threading.Event())
        assert not app.automation_scheduler.active
        lifecycle.resume()
        assert app.automation_scheduler.active
        assert app.execution_coordinator.dispatch_once() == ()
    finally:
        app.close()
