from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from app_server.service_client import ServiceClient, ServiceUnavailable
from app_server.service_state import ServiceFiles, ServiceRecord
from cli.service_cli import start_service, stop_service
from tests.app_server.support import auth, body, control_server


def test_management_requires_authentication_instance_and_local_origin(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            response = await client.get("/health/live")
            assert await response.json() == {"status": "ready"}
            assert (
                await client.post("/control/rpc", json=body("status"))
            ).status == 401
            wrong_instance = {**auth(control), "X-DeepCode-Instance": "wrong"}
            assert (
                await client.post(
                    "/control/rpc", headers=wrong_instance, json=body("status")
                )
            ).status == 401
            for override in [
                {"Origin": "https://other.example"},
                {"Host": "other.example"},
            ]:
                response = await client.post(
                    "/control/rpc",
                    headers={**auth(control), **override},
                    json=body("status"),
                )
                assert response.status == 403
            response = await client.post(
                "/control/rpc", headers=auth(control), json=body("status")
            )
            value = await response.json()
            assert value["result"]["instanceId"] == control.record.instance_id
            assert value["result"]["activeTurns"] == 0
            assert "b" * 64 not in json.dumps(value)
            assert str(tmp_path) not in json.dumps(value)

    asyncio.run(scenario())


def test_oversized_integer_is_a_parse_error_not_an_unhandled_http_failure(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            raw = '{"jsonrpc":"2.0","method":"status","id":' + "9" * 5000 + "}"
            response = await client.post(
                "/control/rpc", headers=auth(control), data=raw
            )
            assert response.status == 400
            assert (await response.json())["error"]["code"] == -32700

    asyncio.run(scenario())


def test_supervisor_can_drain_and_resume_without_stopping_the_host(tmp_path):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            response = await client.post(
                "/control/rpc",
                headers=auth(control),
                json=body("drain", {"timeout": 0}),
            )
            assert (await response.json())["result"]["accepted"]
            assert control.phase == "drained"
            assert not control.stopped.is_set()
            response = await client.post(
                "/control/rpc", headers=auth(control), json=body("resume")
            )
            assert (await response.json())["result"]["accepted"]
            assert control.phase == "ready"
            assert not control.stopped.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "params",
    [
        {"timeout": -1},
        {"timeout": float("nan")},
        {"timeout": 10**100},
        {"cancelRunning": "yes"},
        {"unused": True},
    ],
)
def test_invalid_stop_never_changes_service_state(tmp_path, params):
    async def scenario():
        async with control_server(tmp_path) as (control, client):
            response = await client.post(
                "/control/rpc", headers=auth(control), json=body("stop", params)
            )
            assert "error" in await response.json()
            assert control.phase == "ready"
            assert not control.stopped.is_set()

    asyncio.run(scenario())


def test_bad_identity_never_receives_the_management_credential(tmp_path):
    async def scenario():
        received = []

        async def handler(request):
            received.append((request.path, request.headers.get("Authorization")))
            return web.json_response({"proof": "untrusted"})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        async with TestServer(app) as server:
            files = ServiceFiles(tmp_path / "state.sqlite3")
            lease = files.acquire()
            assert lease is not None
            try:
                files.publish(
                    ServiceRecord(
                        "a" * 32, str(files.database), os.getpid(), server.port
                    ),
                    "b" * 64,
                )
                with pytest.raises(ServiceUnavailable, match="identity"):
                    await asyncio.to_thread(ServiceClient(files).call, "status")
                assert received == [("/control/identity", None)]
                received.clear()
                with pytest.raises(ServiceUnavailable, match="instance changed"):
                    await asyncio.to_thread(
                        ServiceClient(files).call, "status", instance_id="c" * 32
                    )
                assert received == []
            finally:
                files.clear()
                lease.close()

    asyncio.run(scenario())


def test_service_record_is_private_and_rejects_non_loopback_injection(tmp_path):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    with files.acquire():
        record = ServiceRecord(
            secrets.token_hex(16), str(files.database), os.getpid(), 3081
        )
        files.publish(record, "b" * 64)
        assert files.read() == (record, "b" * 64)
        if os.name != "nt":
            assert files.token.stat().st_mode & 0o777 == 0o600
            assert files.record.stat().st_mode & 0o777 == 0o600
        data = json.loads(files.record.read_text())
        data["url"] = "https://other.example"
        files.record.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="discovery"):
            files.read()


def test_background_start_is_idempotent_and_does_not_depend_on_stdin(tmp_path):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [
                pool.submit(start_service, files, port=0, timeout=15) for _ in range(2)
            ]
            first, second = [attempt.result(timeout=20) for attempt in attempts]
        assert second["instanceId"] == first["instanceId"]
        assert second["pid"] == first["pid"]
        assert second["schedulerActive"] is True
        assert stop_service(files, timeout=2, cancel_running=False) == {
            "phase": "stopped"
        }
        assert not files.running()
        assert files.read() is None
        assert "Traceback" not in files.log.read_text()
    finally:
        if files.running():
            stop_service(files, timeout=0, cancel_running=True)


@pytest.mark.parametrize("log_to_file", [False, True])
@pytest.mark.parametrize("detached", [False, True])
def test_service_exits_cleanly_after_control_stop(tmp_path, log_to_file, detached):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import faulthandler, runpy; "
            "faulthandler.dump_traceback_later(12); "
            "runpy.run_module('app_server.service', run_name='__main__')",
            "--database",
            str(files.database),
            "--port",
            "0",
            *(["--log-file"] if log_to_file else []),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[2],
        start_new_session=detached and os.name != "nt",
        creationflags=(
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            if detached and os.name == "nt"
            else 0
        ),
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                status = ServiceClient(files).call("status", timeout=1)
                assert status["phase"] == "ready"
                break
            except ServiceUnavailable:
                assert time.monotonic() < deadline and process.poll() is None
                time.sleep(0.05)
        stop_service(files, timeout=2, cancel_running=False)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr.decode()
        assert stdout == b""
        assert b"Traceback" not in stderr
    except BaseException:
        if process.poll() is None:
            process.kill()
        _, stderr = process.communicate(timeout=5)
        print(stderr.decode(errors="replace"))
        if files.log.exists():
            print(files.log.read_text(encoding="utf-8")[-4000:])
        raise
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_cli_launcher_can_exit_while_the_service_remains_available(tmp_path):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    entry = Path(__file__).resolve().parents[2] / "deepcode.py"

    def cli(command, *arguments):
        result = subprocess.run(
            [
                sys.executable,
                str(entry),
                "service",
                command,
                "--database",
                str(files.database),
                "--json",
                *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    try:
        launched = cli("start", "--port", "0")
        assert cli("status")["instanceId"] == launched["instanceId"]
        assert cli("stop", "--drain", "--timeout", "2")["phase"] == "stopped"
    finally:
        if files.running():
            stop_service(files, timeout=0, cancel_running=True)


@pytest.mark.parametrize("_attempt", range(5))
def test_stale_crash_record_with_a_brief_status_lock_still_restarts(tmp_path, _attempt):
    from app_server.service import serve
    from app_server.service_state import ServiceRecord
    from cli.service_cli import _wait_ready

    async def scenario():
        files = ServiceFiles(tmp_path / "state.sqlite3")
        files.publish(
            ServiceRecord("a" * 32, str(files.database), os.getpid(), 9), "b" * 64
        )
        probe = files.acquire()
        assert probe is not None
        task = asyncio.create_task(serve(files, 0))
        try:
            await asyncio.sleep(0.05)
            waiting_for_probe = not task.done()
            probe.close()
            assert waiting_for_probe, (
                "A stale record is not proof that the lock belongs to a live daemon"
            )
            # Exercise the launcher's actual probe policy and the same startup
            # budget as the other service tests, including Windows ACL setup.
            ready = await asyncio.to_thread(_wait_ready, ServiceClient(files), 15)
            assert ready["instanceId"] != "a" * 32
            await asyncio.to_thread(
                ServiceClient(files).call, "stop", {"timeout": 1, "cancelRunning": True}
            )
            await asyncio.wait_for(task, 5)
        finally:
            probe.close()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_unverified_busy_owner_is_a_failure_so_supervisor_can_retry(tmp_path):
    from app_server.service import serve

    files = ServiceFiles(tmp_path / "state.sqlite3")
    with files.acquire():
        files.publish(
            ServiceRecord("a" * 32, str(files.database), os.getpid(), 9), "b" * 64
        )
        with pytest.raises(ServiceUnavailable, match="no live owner"):
            asyncio.run(serve(files, 0))
    assert files.record.exists()  # A non-owner cannot clear another owner's state.


def test_start_does_not_wait_for_a_status_probe_that_has_already_left(
    tmp_path, monkeypatch
):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    running = files.running
    checks = 0

    def briefly_busy():
        nonlocal checks
        checks += 1
        return True if checks == 1 else running()

    monkeypatch.setattr(files, "running", briefly_busy)
    try:
        status = start_service(files, port=0, timeout=10)
        assert status["phase"] == "ready"
        assert running()
    finally:
        if running():
            stop_service(files, timeout=2, cancel_running=True)


def test_same_port_restart_after_server_closes_a_live_connection(tmp_path, monkeypatch):
    import socket

    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEEPCODE_SESSIONS_DIR", str(tmp_path / "sessions"))
    files = ServiceFiles(tmp_path / "state.sqlite3")
    try:
        status = start_service(files, port=0, timeout=15)
        port = int(status["url"].rsplit(":", 1)[1])
        for _ in range(3):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                client.sendall(
                    f"GET /health/live HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
                )
                assert b"200 OK" in client.recv(4096)
                stop_service(files, timeout=5, cancel_running=False)
                while client.recv(4096):
                    pass
            # The server actively closed the connection; immediately reuse its
            # fixed port while the previous TCP connection is in TIME_WAIT.
            previous = status
            status = start_service(files, port=port, timeout=15)
            assert status["url"] == previous["url"]
            assert status["instanceId"] != previous["instanceId"]
            assert status["phase"] == "ready"
            if os.name != "nt":
                with socket.socket() as competitor:
                    competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    with pytest.raises(OSError):
                        competitor.bind(("127.0.0.1", port))
    finally:
        if files.running():
            stop_service(files, timeout=5, cancel_running=True)


def test_probe_errors_identify_transport_failure_without_echoing_secrets(monkeypatch):
    from types import SimpleNamespace

    import httpx

    record = ServiceRecord("a" * 32, "unused.sqlite3", 1, 3081)
    files = SimpleNamespace(running=lambda: True, read=lambda: (record, "secret-token"))

    def fail(**kwargs):
        raise httpx.ConnectError("request contained secret-token")

    monkeypatch.setattr(httpx, "Client", fail)
    with pytest.raises(ServiceUnavailable, match="ConnectError") as error:
        ServiceClient(files).call("status")
    assert "secret-token" not in str(error.value)


def test_startup_timeout_keeps_the_last_probe_failure(tmp_path):
    from types import SimpleNamespace

    from cli.service_cli import _wait_ready

    def fail(*args, **kwargs):
        raise ServiceUnavailable("Cannot communicate with the local service (HTTP 403)")

    client = SimpleNamespace(
        call=fail, files=SimpleNamespace(log=tmp_path / "service.log")
    )
    with pytest.raises(ServiceUnavailable, match="Last check:.*HTTP 403"):
        _wait_ready(client, timeout=0)
