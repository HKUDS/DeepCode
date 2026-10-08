"""Service round trips never stall typing, completion, or the status line."""

from __future__ import annotations

import asyncio
import io
import sys
import threading
import time
from types import SimpleNamespace

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

import cli.tui.app as tui_app
import cli.tui.input as tui_input
from cli.tui import commands
from cli.tui.input import InputReader


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


def _app_with_sessions(session_ids, *, delay=0.0):
    app = object.__new__(tui_app.TuiApp)
    app._completion_cache = {}
    app._completion_loads = {}
    app._completion_cache_lock = threading.Lock()
    listings = []

    def list_recent(*, limit, include_all):
        listings.append(1)
        time.sleep(delay)
        return [SimpleNamespace(session_id=value) for value in session_ids]

    app.thread_client = SimpleNamespace(list_recent=list_recent)
    return app, listings


def test_a_slow_service_holds_up_neither_typing_nor_enter(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr(tui_input, "_HISTORY_PATH", tmp_path / "history")
    app, listings = _app_with_sessions(["abc12345"], delay=1.5)

    async def scenario():
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            reader = InputReader(str(tmp_path))
            reader.set_argument_provider(app._complete_command_argument)
            task = asyncio.create_task(reader.read())
            await asyncio.sleep(0.1)
            started = time.monotonic()
            for character in "/resume abc":
                pipe.send_text(character)
                await asyncio.sleep(0.02)
            pipe.send_text("\r")
            line = await asyncio.wait_for(task, 5)
            return line, time.monotonic() - started

    line, elapsed = asyncio.run(scenario())

    assert line == "/resume abc"
    assert listings  # the service was asked for candidates ...
    assert elapsed < 1.0  # ... without the keystrokes or Enter waiting on it


def test_a_slow_candidate_fetch_serves_the_next_keystroke():
    app, listings = _app_with_sessions(["abc12345"], delay=0.4)

    assert commands._session_id_completions(app, "a") == []  # still loading
    time.sleep(0.6)
    assert commands._session_id_completions(app, "ab") == ["abc12345"]
    assert len(listings) == 1


def test_completion_reuses_the_session_list_but_commands_read_it_fresh():
    session_ids = ["abc12345"]
    app, listings = _app_with_sessions(session_ids)

    for prefix in ("a", "ab", "abc"):
        assert commands._session_id_completions(app, prefix) == ["abc12345"]
    assert len(listings) == 1

    session_ids.append("abd99999")
    # A command resolving a prefix must see the Session created a moment ago.
    assert commands._resolve_session_prefix(app, "abd") == "abd99999"


def test_thread_events_do_not_reread_the_session_file(monkeypatch):
    built = []

    class Bridge:
        def __init__(self, *, store, session_id, workspace):
            built.append(session_id)
            self.store = store
            self.session_id = session_id

    monkeypatch.setattr(tui_app, "SessionBridge", Bridge)
    app = object.__new__(tui_app.TuiApp)
    app.workspace = "/ws"
    app._leased_session_id = None
    app._session_activity = None
    app._lease_session = lambda session_id: None
    thread = SimpleNamespace(id="s1", reasoning_effort=None, context_window=None)
    app.thread_client = SimpleNamespace(
        store=object(),
        thread=thread,
        execution_profile=SimpleNamespace(model_id="m", connection_id="c"),
    )

    app._sync_thread_state()
    app._sync_thread_state()  # a Turn started
    app._sync_thread_state()  # ... and ended
    assert built == ["s1"]

    app.thread_client.thread = SimpleNamespace(
        id="s2", reasoning_effort=None, context_window=None
    )
    app._sync_thread_state()  # /new or /resume
    assert built == ["s1", "s2"]


def test_sending_runs_off_the_event_loop_in_service_mode():
    app = object.__new__(tui_app.TuiApp)
    app.workspace = "/ws"
    app.reader = SimpleNamespace(interactive=True)
    app.selected_skill_ids = []
    app._prompted_approval_ids = set()
    app._respond_to_pending_approval = lambda text: None
    app._print_status = lambda status: None
    senders = []

    def send(text, *, skill_ids):
        senders.append(threading.current_thread())
        return SimpleNamespace(kind="started")

    app.thread_client = SimpleNamespace(runtime_mode="service", send=send)

    asyncio.run(app._handle_line("hello"))

    assert senders and senders[0] is not threading.main_thread()
