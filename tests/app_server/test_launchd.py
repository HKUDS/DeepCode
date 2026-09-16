from __future__ import annotations

import json
import os
import plistlib
import signal
import subprocess
import sys
import time

import pytest

from app_server.launchd import LaunchAgent
from app_server.service_client import (
    ServiceClient,
    ServiceOperationError,
    ServiceUnavailable,
)
from app_server.service_state import ServiceFiles, ServiceRecord
from cli import service_cli

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS LaunchAgent")


def agent_with_fake_launchctl(tmp_path, monkeypatch):
    agent = LaunchAgent(
        ServiceFiles(tmp_path / "state.sqlite3"), directory=tmp_path / "LaunchAgents"
    )
    state = {"loaded": False, "commands": []}

    def run(*args):
        state["commands"].append(args)
        code, out, error = 0, "", ""
        if args == ("print", agent.target):
            if state["loaded"]:
                pid = state.get("pid", 1234)
                out = (
                    "state = waiting\n"
                    if pid is None
                    else f"state = running\n\tpid = {pid}\n"
                )
            else:
                code, error = 113, "Could not find service"
        elif args[0] == "bootstrap":
            state["loaded"] = True
        elif args[0] == "bootout":
            state["loaded"] = False
        return subprocess.CompletedProcess(args, code, out, error)

    monkeypatch.setattr(agent, "_run", run)
    return agent, state


def test_install_is_opt_in_idempotent_and_does_not_copy_credentials(
    tmp_path, monkeypatch
):
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-api-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:private-password@proxy.invalid")
    result = agent.install(port=3081, path="/opt/homebrew/bin:/usr/bin:/bin")
    first = agent.path.read_bytes()
    plist = plistlib.loads(first)
    assert plist["ProgramArguments"][0] == os.path.abspath(sys.executable)
    assert plist["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert b"private-test-api-key" not in first
    assert b"private-password" not in first
    assert not state["loaded"]
    assert agent.install(port=3081, path="/opt/homebrew/bin:/usr/bin:/bin") == result
    assert agent.path.read_bytes() == first
    assert agent.path.stat().st_mode & 0o777 == 0o600
    report = agent.doctor()
    assert "OPENAI_API_KEY" in report["shellOnlyVariables"]
    assert "private-test-api-key" not in json.dumps(report)


def test_start_and_unload_use_one_job_and_refuse_live_configuration_changes(
    tmp_path, monkeypatch
):
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    agent.install(port=3081)
    agent.start()
    assert ("bootstrap", agent.domain, str(agent.path)) in state["commands"]
    agent.start()
    assert ("kickstart", agent.target) in state["commands"]
    assert not any("-k" in command for command in state["commands"])
    previous = agent.path.read_bytes()
    with pytest.raises(ServiceOperationError, match="Stop"):
        agent.install(port=3082)
    with pytest.raises(ServiceOperationError, match="port"):
        agent.start(port=3082)
    assert agent.path.read_bytes() == previous
    agent.unload()
    assert not state["loaded"]
    assert not agent.uninstall()["installed"]
    assert not agent.path.exists()


def test_doctor_reports_missing_executable_without_starting_a_job(
    tmp_path, monkeypatch
):
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    agent.install(port=3081)
    value = plistlib.loads(agent.path.read_bytes())
    value["ProgramArguments"][0] = str(tmp_path / "missing-python")
    agent.path.write_bytes(plistlib.dumps(value))
    report = agent.doctor()
    assert any(
        check["name"] == "executable" and not check["ok"] for check in report["checks"]
    )
    with pytest.raises(ServiceOperationError, match="missing"):
        agent.start()
    assert not state["loaded"]


def test_install_and_uninstall_leave_an_existing_manual_service_running(
    tmp_path, monkeypatch, capsys
):
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    files = agent.files
    monkeypatch.setattr(service_cli, "_service_manager", lambda _: agent)
    try:
        first = service_cli.start_service(files, port=0)
        assert (
            service_cli.run(
                ["install", "--at-login", "--database", str(files.database), "--json"]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["installed"]
        assert not state["loaded"]
        assert ServiceClient(files).call("status")["instanceId"] == first["instanceId"]
        assert (
            service_cli.run(["uninstall", "--database", str(files.database), "--json"])
            == 0
        )
        assert not json.loads(capsys.readouterr().out)["installed"]
        assert ServiceClient(files).call("status")["instanceId"] == first["instanceId"]
    finally:
        service_cli.stop_service(files, timeout=0, cancel_running=True)


def test_stop_drains_manual_service_even_when_an_idle_launch_job_exists(
    tmp_path, monkeypatch
):
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    monkeypatch.setattr(service_cli, "_service_manager", lambda _: agent)
    try:
        service_cli.start_service(agent.files, port=0)
        state.update(loaded=True, pid=None)
        assert (
            service_cli.stop_service(agent.files, timeout=2, cancel_running=False)[
                "phase"
            ]
            == "stopped"
        )
        assert not state["loaded"]
        assert not agent.files.running()
    finally:
        service_cli.stop_service(agent.files, timeout=0, cancel_running=True)


@pytest.mark.parametrize("failure", ["drain", "unload"])
def test_failed_managed_stop_preserves_work_and_restores_admission(
    tmp_path, monkeypatch, failure
):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    agent, state = agent_with_fake_launchctl(tmp_path, monkeypatch)
    state["loaded"] = True
    calls = []

    class Client:
        def call(self, method, *args, **kwargs):
            calls.append(method)
            if method == failure:
                raise ServiceOperationError("drain timed out")
            return {"instanceId": "a" * 32}

    def unload():
        calls.append("unload")
        raise ServiceOperationError("bootout failed")

    monkeypatch.setattr(service_cli, "_service_manager", lambda _: agent)
    monkeypatch.setattr(service_cli, "ServiceClient", lambda _: Client())
    monkeypatch.setattr(agent, "unload", unload)
    with files.acquire():
        files.publish(
            ServiceRecord("a" * 32, str(files.database), 1234, 3081), "b" * 64
        )
        with pytest.raises(ServiceOperationError):
            service_cli.stop_service(files, timeout=0, cancel_running=False)
    assert calls == (["drain"] if failure == "drain" else ["drain", "unload", "resume"])
    assert state["loaded"]


@pytest.mark.skipif(
    os.environ.get("DEEPCODE_TEST_LAUNCHD") != "1", reason="opt-in native launchd test"
)
def test_native_job_recovers_from_crash_and_stays_stopped(tmp_path, monkeypatch):
    files = ServiceFiles(tmp_path / "state.sqlite3")
    agent = LaunchAgent(files, directory=tmp_path / "LaunchAgents")
    if not agent.available():
        pytest.skip("No macOS GUI login domain")
    monkeypatch.setattr(service_cli, "_service_manager", lambda _: agent)
    agent.install(port=0)
    try:
        first = service_cli.start_service(files, timeout=20)
        assert agent.job()["pid"] == first["pid"]
        assert files.read()[0].database == str(files.database)
        os.kill(first["pid"], signal.SIGKILL)
        deadline = time.monotonic() + 30
        while True:
            try:
                second = ServiceClient(files).call("status", timeout=1)
                if second["instanceId"] != first["instanceId"]:
                    break
            except ServiceUnavailable:
                pass
            assert time.monotonic() < deadline, (
                files.log.read_text() if files.log.exists() else "No service log"
            )
            time.sleep(0.1)
        assert agent.job()["pid"] == second["pid"]
        assert (
            service_cli.stop_service(files, timeout=2, cancel_running=False)["phase"]
            == "stopped"
        )
        time.sleep(11)  # Beyond the configured restart throttle.
        assert not agent.job()["loaded"]
        assert not files.running()
    finally:
        agent.unload()
        agent.uninstall()
