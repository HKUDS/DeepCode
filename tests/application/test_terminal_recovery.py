from __future__ import annotations

import os
import shlex
import sys
import threading
import time

import pytest

from core.application import DeepCodeApplication
from core.application.errors import InvalidArgumentError, TerminalNotFoundError
from core.domain import TrustState

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX PTY adapter only")


@pytest.fixture
def terminal_app(tmp_path):
    app = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
    thread = app.threads.start(project.id, title="PTY recovery")
    try:
        yield app, thread.id, workspace
    finally:
        app.close()


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("terminal did not reach the expected state")


def execute(app, thread_id, workspace, source):
    (workspace / "output.py").write_text(source)
    terminal = app.terminals.create(thread_id)
    session = app.terminals._sessions[terminal.id]
    app.terminals.write(
        thread_id, terminal.id, f"exec {shlex.quote(sys.executable)} output.py\r"
    )
    return terminal, session


def test_real_utf8_output_survives_exit_and_pages_without_loss(terminal_app):
    app, thread_id, workspace = terminal_app
    notifications = []
    app.terminals.subscribe(
        lambda method, payload: notifications.append((method, payload))
    )
    terminal, session = execute(
        app,
        thread_id,
        workspace,
        "import sys, time\n"
        "data = '汉🙂字\\n'.encode()\n"
        "sys.stdout.buffer.write(data[:2]); sys.stdout.buffer.flush(); time.sleep(.05)\n"
        "sys.stdout.buffer.write(data[2:]); sys.stdout.buffer.flush()\n"
        "open('executions.txt', 'a').write('once\\n')\n",
    )
    wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
    session.reader.join(2)
    assert not session.reader.is_alive()
    assert session.master_fd is None
    assert app.terminals.active_count == 0
    assert app.terminals.list(thread_id)[0]["exited"] is True
    output = b""
    offset = 0
    while True:
        page = app.terminals.read(thread_id, terminal.id, offset=offset, limit=4)
        encoded = page["data"].encode()
        assert page["offset"] == offset
        assert page["nextOffset"] == offset + len(encoded)
        output += encoded
        offset = page["nextOffset"]
        if not page["hasMore"]:
            break
    live = b"".join(
        payload["data"].encode()
        for method, payload in notifications
        if method == "terminal.output"
    )
    assert output == live
    assert "汉🙂字" in output.decode()
    assert "�" not in output.decode()
    assert (workspace / "executions.txt").read_text() == "once\n"
    with pytest.raises(InvalidArgumentError):
        app.terminals.read(thread_id, terminal.id, offset=offset + 1)
    byte_inside = output.index("汉".encode()) + 1
    with pytest.raises(InvalidArgumentError):
        app.terminals.read(thread_id, terminal.id, offset=byte_inside)
    with pytest.raises(InvalidArgumentError):
        app.terminals.read(thread_id, terminal.id, through=byte_inside)
    other = app.threads.start(app.threads.read(thread_id).project_id, title="Other")
    with pytest.raises(TerminalNotFoundError):
        app.terminals.read(other.id, terminal.id)


def test_real_overflow_is_bounded_and_reports_exact_retained_tail(terminal_app):
    app, thread_id, workspace = terminal_app
    app.terminals.output_capacity = 1024
    notifications = []
    app.terminals.subscribe(
        lambda method, payload: notifications.append((method, payload))
    )
    terminal, session = execute(app, thread_id, workspace, "print('🙂汉字' * 3000)\n")
    wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
    session.reader.join(2)
    page = app.terminals.read(thread_id, terminal.id)
    assert page["truncated"] and page["availableFrom"] > 0
    assert len(session.output) <= 1024
    live = b"".join(
        payload["data"].encode()
        for method, payload in notifications
        if method == "terminal.output"
    )
    assert page["data"].encode() == live[page["availableFrom"] :]
    assert "�" not in page["data"]
    # An evicted cutoff cannot claim missing output is available or loop.
    old = app.terminals.read(thread_id, terminal.id, through=4)
    assert old["truncated"] and old["data"] == "" and not old["hasMore"]
    assert old["nextOffset"] == old["availableFrom"]


def test_completed_retention_does_not_consume_live_capacity(terminal_app):
    app, thread_id, workspace = terminal_app
    app.terminals.max_sessions = 1
    app.terminals.retained_exits = 2
    ids = []
    for _ in range(3):
        terminal, session = execute(app, thread_id, workspace, "print('done')\n")
        wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
        session.reader.join(2)
        ids.append(terminal.id)
    assert [
        entry["terminal"]["terminalId"] for entry in app.terminals.list(thread_id)
    ] == ids[1:]
    with pytest.raises(TerminalNotFoundError):
        app.terminals.read(thread_id, ids[0])


def test_explicit_close_cleans_up_a_running_pty(terminal_app):
    app, thread_id, workspace = terminal_app
    terminal, session = execute(
        app,
        thread_id,
        workspace,
        "import time\nprint('ready', flush=True)\ntime.sleep(60)\n",
    )
    wait_for(lambda: "ready" in app.terminals.read(thread_id, terminal.id)["data"])
    assert app.terminals.close(thread_id, terminal.id)
    wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
    session.reader.join(2)
    assert session.process.poll() is not None
    assert session.master_fd is None
    assert not session.reader.is_alive()


def test_reader_start_failure_releases_process_capacity_and_lease(
    terminal_app, monkeypatch
):
    app, thread_id, workspace = terminal_app
    app.terminals.max_sessions = 1
    original = threading.Thread.start

    def fail_reader(thread):
        if thread.name.startswith("deepcode-terminal-"):
            raise RuntimeError("injected thread startup failure")
        return original(thread)

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_reader)
        with pytest.raises(RuntimeError, match="startup failure"):
            app.terminals.create(thread_id)
    assert app.terminals.active_count == 0
    assert app.terminals.list(thread_id) == []
    terminal, session = execute(
        app, thread_id, workspace, "print('capacity recovered')\n"
    )
    wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
    session.reader.join(2)
    assert session.master_fd is None


def test_service_restart_does_not_fabricate_old_terminal_sessions(terminal_app):
    app, thread_id, workspace = terminal_app
    terminal, session = execute(app, thread_id, workspace, "print('old output')\n")
    wait_for(lambda: app.terminals.read(thread_id, terminal.id)["exited"])
    session.reader.join(2)
    database_path = app.database.path
    app.close()
    reopened = DeepCodeApplication.open(database_path)
    try:
        assert reopened.terminals.list(thread_id) == []
        with pytest.raises(TerminalNotFoundError):
            reopened.terminals.read(thread_id, terminal.id)
    finally:
        reopened.close()
