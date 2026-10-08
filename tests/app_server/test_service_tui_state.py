"""The TUI shows and carries the Session's real model and access."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

import cli.tui.app as tui_app
from cli.service_thread_client import ServiceThreadClient
from cli.tui import commands
from tests.app_server.support import PausedFactory, control_server
from tests.app_server.test_native_client import publish


def _client(files, workspace) -> ServiceThreadClient:
    return ServiceThreadClient(
        workspace=str(workspace),
        model=None,
        connection_id=None,
        reasoning_effort=None,
        max_iterations=None,
        streaming=True,
        trust_workspace=True,
        database=files.database,
    )


def _app(client, workspace) -> tui_app.TuiApp:
    app = object.__new__(tui_app.TuiApp)
    app.console = Console(file=io.StringIO(), color_system=None, width=200)
    app.reader = SimpleNamespace(interactive=False)
    app.thread_client = client
    app.workspace = str(workspace)
    app._leased_session_id = None
    app._session_activity = None
    app._sync_thread_state()
    return app


def test_new_session_follows_the_defaults_unless_a_model_was_chosen(tmp_path):
    async def scenario():
        async with control_server(tmp_path, session_factory=PausedFactory()) as (
            control,
            _,
        ):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            client = None
            try:
                client = await asyncio.to_thread(_client, files, workspace)
                assert client.thread.model is None

                await asyncio.to_thread(client.new_thread)
                # The resolved default must not be pinned onto the new Session.
                assert client.thread.model is None
                assert client.thread.connection_id is None

                chosen = client.model
                await asyncio.to_thread(
                    client.switch_execution,
                    connection_id=client.execution_profile.connection_id,
                    model=chosen,
                    reasoning_effort=None,
                    context_window=None,
                )
                await asyncio.to_thread(client.new_thread)
                # An explicit /model choice carries over, as before.
                assert client.thread.model == chosen
            finally:
                if client is not None:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def _run_command(handler, app, args=""):
    # The command makes blocking service calls, exactly as in the TUI. Run it
    # on its own loop: the in-process test service shares this one.
    return asyncio.to_thread(asyncio.run, handler(app, args))


def test_permissions_shows_a_default_access_changed_elsewhere(tmp_path):
    async def scenario():
        async with control_server(tmp_path, session_factory=PausedFactory()) as (
            control,
            _,
        ):
            files, lease = publish(control)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            client = app = None
            try:
                client = await asyncio.to_thread(_client, files, workspace)
                app = await asyncio.to_thread(_app, client, workspace)
                before = await _run_command(commands._cmd_permissions, app)
                assert "effective: default (ask)" in before

                # Settings emits no Session event; nothing pushes this change.
                await asyncio.to_thread(
                    control.host.application.settings.update,
                    {"security": {"accessPreset": "read_only"}},
                    scope="user",
                )

                after = await _run_command(commands._cmd_permissions, app)
                assert "effective: default (read only)" in after
            finally:
                if app is not None and app._session_activity is not None:
                    app._session_activity.close()
                if client is not None:
                    await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "handler", ["_cmd_model", "_cmd_effort", "_cmd_context", "_cmd_permissions"]
)
def test_state_commands_report_an_unreadable_service(handler):
    calls = []

    async def refresh():
        calls.append("refresh")
        return "Could not read the current settings: service down"

    app = SimpleNamespace(
        refresh_execution_state=refresh,
        reader=SimpleNamespace(interactive=False),
    )

    result = asyncio.run(getattr(commands, handler)(app, ""))

    assert calls == ["refresh"]
    assert result == "Could not read the current settings: service down"
