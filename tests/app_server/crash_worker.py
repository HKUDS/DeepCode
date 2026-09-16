"""Deterministic running Agent inside the real service, for process crash tests."""

import asyncio
from pathlib import Path
import sys
import threading

from app_server.service import serve
from app_server.service_state import ServiceFiles
from core.application.application import DeepCodeApplication
from core.events import Event, TurnStarted


class CheckpointFactory:
    def create(self, *, workspace, model, approval_callback, injection_callback):
        class Session:
            def load_history(self, _history):
                pass

            async def run_stream(self, _operation):
                yield Event("start", TurnStarted())
                with (Path(workspace) / "executions.txt").open("a") as stream:
                    stream.write("started\n")
                await asyncio.Event().wait()

            async def aclose(self):
                pass

        return Session()


if __name__ == "__main__":
    original_open = DeepCodeApplication.open

    def open_for_test(*args, **kwargs):
        app = original_open(*args, session_factory=CheckpointFactory(), **kwargs)
        if "--pause-steer" in sys.argv:
            append = app.turns.turn_inputs._append_canonical_input

            def pause_steer(item):
                append(item)
                (Path(sys.argv[1]).parent / "steer-pending").touch()
                threading.Event().wait()

            app.turns.turn_inputs._append_canonical_input = pause_steer
        return app

    # Only this child process substitutes the Agent. Listener, leases, executor,
    # SQLite, Session files and restart recovery all use production code.
    DeepCodeApplication.open = staticmethod(open_for_test)
    asyncio.run(serve(ServiceFiles(Path(sys.argv[1])), 0))
