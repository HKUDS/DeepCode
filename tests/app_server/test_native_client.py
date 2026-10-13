from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace

import pytest

from app_server.errors import RpcError
from app_server.native_client import NativeRpcClient
from app_server.protocol.codec import encode_message
from app_server.service_client import ServiceClient
from app_server.service_state import ServiceFiles, ServiceRecord
from core.domain import TrustState
from core.version import __version__
from tests.app_server.support import PausedFactory, control_server

INITIALIZE = {
    "protocolVersion": "1.0",
    "clientInfo": {"name": "native-test", "version": "1", "surface": "desktop"},
}


def publish(control):
    files = ServiceFiles(control.host.application.database.path)
    lease = files.acquire()
    assert lease is not None
    files.publish(control.record, "b" * 64)
    return files, lease


def test_native_detach_keeps_task_and_context_cap(tmp_path):
    async def scenario():
        factory = PausedFactory()
        async with control_server(tmp_path, session_factory=factory) as (control, _):
            files, lease = publish(control)
            first = NativeRpcClient(files)
            second = NativeRpcClient(files)
            try:
                workspace = tmp_path / "workspace"
                workspace.mkdir()
                project = control.host.application.projects.add(
                    str(workspace), trust_state=TrustState.TRUSTED
                )
                await first.connect(INITIALIZE)
                thread = (
                    await first.request(
                        "thread/start",
                        {
                            "projectId": project.id,
                            "title": "Native task",
                            "contextWindow": 32000,
                        },
                    )
                )["thread"]
                assert thread["contextWindow"] == 32000
                result = await first.request(
                    "turn/start",
                    {
                        "threadId": thread["id"],
                        "prompt": "finish after detach",
                        "messageId": "native-once",
                    },
                )
                assert await asyncio.to_thread(factory.started.wait, 3)
                await first.close()
                factory.release.set()
                assert await asyncio.to_thread(factory.finished.wait, 3)
                await second.connect(INITIALIZE)
                receipt = await second.request(
                    "turn/input/read",
                    {"threadId": thread["id"], "messageId": "native-once"},
                )
                assert receipt["item"]["turnId"] == result["turn"]["id"]
                assert (
                    workspace / "completed.txt"
                ).read_text() == "completed after disconnect"
                assert (await asyncio.to_thread(ServiceClient(files).call, "status"))[
                    "instanceId"
                ] == control.record.instance_id
            finally:
                factory.release.set()
                await first.close()
                await second.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("shutdown", [False, True])
def test_real_stdio_attachment_exit_preserves_service(tmp_path, shutdown):
    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "app_server",
                    "--database",
                    str(files.database),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "DEEPCODE_HOME": str(tmp_path / "home")},
                )
                requests = [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": INITIALIZE,
                    },
                    {"jsonrpc": "2.0", "id": 2, "method": "project/list", "params": {}},
                ]
                if shutdown:
                    requests.append(
                        {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}}
                    )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(b"".join(map(encode_message, requests))), 15
                )
                assert process.returncode == 0, stderr.decode()
                replies = {
                    x["id"]: x
                    for x in map(json.loads, stdout.splitlines())
                    if "id" in x
                }
                assert (
                    replies[1]["result"]["serviceInfo"]["shutdownScope"] == "connection"
                )
                assert "result" in replies[2]
                assert files.running()
                assert (await asyncio.to_thread(ServiceClient(files).call, "status"))[
                    "instanceId"
                ] == control.record.instance_id
            finally:
                if "process" in locals() and process.returncode is None:
                    process.kill()
                    await process.wait()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_native_version_mismatch_does_not_replace_service(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            client = NativeRpcClient(files)
            try:
                files.publish(replace(control.record, version="0.0.0"), "b" * 64)
                with pytest.raises(RpcError, match="different versions") as error:
                    await client.connect(INITIALIZE)
                assert error.value.stable_code == "SERVICE_VERSION_MISMATCH"
                assert files.running()
                assert not control.business._connections
            finally:
                await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())


def _claim_version(files, version):
    """Make the running service's discovery record claim another release."""
    record, token = files.read()
    files.publish(replace(record, version=version), token)
    return record


def _connect_starting(files):
    async def connect():
        client = NativeRpcClient(files)
        try:
            return await client.connect(INITIALIZE, start=True)
        finally:
            await client.close()

    return asyncio.run(connect())


@pytest.fixture
def running_service(tmp_path):
    from cli.service_cli import start_service, stop_service

    files = ServiceFiles(tmp_path / "state.sqlite3")
    try:
        start_service(files, port=0, timeout=15)
        yield files
    finally:
        if files.running():
            stop_service(files, timeout=5, cancel_running=True)


def test_a_newer_client_restarts_an_idle_older_service(running_service):
    # After an update, Desktop and the TUI met the service the old version had
    # started, and could only tell the user to run a terminal command.
    older = _claim_version(running_service, "0.0.1")
    info = _connect_starting(running_service)
    assert info["serviceInfo"]["instanceId"] != older.instance_id
    record, _ = running_service.read()
    assert record.version == __version__
    assert record.port == older.port


def test_an_older_client_never_restarts_a_newer_service(running_service):
    # The newer service may have migrated the database past what we can open.
    newer = _claim_version(running_service, "999.0.0")
    with pytest.raises(RpcError, match="This client is older") as error:
        _connect_starting(running_service)
    assert error.value.stable_code == "SERVICE_VERSION_MISMATCH"
    status = ServiceClient(running_service).call("status")
    assert status["instanceId"] == newer.instance_id


def test_an_older_client_reconnecting_by_itself_is_told_to_update(running_service):
    _claim_version(running_service, "999.0.0")

    async def reconnect():
        client = NativeRpcClient(running_service)
        try:
            await client.connect(INITIALIZE)
        finally:
            await client.close()

    with pytest.raises(RpcError, match="This client is older"):
        asyncio.run(reconnect())


def test_a_restart_that_keeps_the_old_version_says_why(running_service, monkeypatch):
    import cli.service_cli as service_cli

    # A service manager entry keeps starting the executable it was installed with.
    monkeypatch.setattr(service_cli, "restart_service", lambda *args, **kwargs: None)
    _claim_version(running_service, "0.0.1")
    with pytest.raises(RpcError, match="service manager still starts"):
        _connect_starting(running_service)


@pytest.mark.parametrize("work", ["activeTurns", "queuedTurns", "terminals"])
def test_an_older_service_with_work_in_progress_is_not_restarted(
    tmp_path, monkeypatch, work
):
    import cli.service_cli as service_cli

    def restart(*args, **kwargs):
        raise AssertionError("restarted a service with work in progress")

    monkeypatch.setattr(service_cli, "restart_service", restart)
    files = ServiceFiles(tmp_path / "state.sqlite3")
    record = ServiceRecord("a" * 32, str(files.database), 1, 9, version="0.0.1")
    status = {"activeTurns": 0, "queuedTurns": 0, "terminals": 0, work: 1}
    with pytest.raises(RpcError, match="has active work"):
        asyncio.run(
            NativeRpcClient(files)._restart_older_service(record, status, allowed=True)
        )


def test_pinned_bundle_survives_source_replacement(tmp_path, monkeypatch):
    from app_server.runtime_install import pinned_service_executable

    source = tmp_path / "desktop-app" / "app-server"
    source.mkdir(parents=True)
    executable = source / "deepcode-app-server"
    executable.write_bytes(b"first binary")
    assets = source / "_internal" / "app_server" / "web_assets"
    assets.mkdir(parents=True)
    (assets / "web-build.json").write_text('{"buildId":"first"}')
    (assets / "index.html").write_text("first page")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "private-home"))
    first = pinned_service_executable()
    assert first != executable
    assert pinned_service_executable() == first
    executable.write_bytes(b"second binary")
    (assets / "index.html").write_text("second page")
    second = pinned_service_executable()
    assert second != first
    assert first.read_bytes() == b"first binary"
    assert (
        first.parent / "_internal/app_server/web_assets/index.html"
    ).read_text() == "first page"
    assert (
        second.parent / "_internal/app_server/web_assets/index.html"
    ).read_text() == "second page"


def test_native_lost_write_is_unknown_and_not_replayed(tmp_path, monkeypatch):
    async def scenario():
        async with control_server(tmp_path) as (control, _):
            files, lease = publish(control)
            client = NativeRpcClient(files)
            calls = []
            try:
                await client.connect(INITIALIZE)
                original = control.host.application.projects.add

                def delayed(*args, **kwargs):
                    import time

                    calls.append(1)
                    result = original(*args, **kwargs)
                    time.sleep(0.15)
                    return result

                monkeypatch.setattr(control.host.application.projects, "add", delayed)
                with pytest.raises(RpcError) as error:
                    await client.request(
                        "project/add", {"path": str(tmp_path)}, timeout=0.03
                    )
                assert error.value.stable_code == "RESULT_UNKNOWN"
                await asyncio.sleep(0.2)
                projects = await client.request("project/list", {})
                assert len(projects["projects"]) == 1
                assert calls == [1]
            finally:
                await client.close()
                files.clear()
                lease.close()

    asyncio.run(scenario())
