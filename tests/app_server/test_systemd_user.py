from __future__ import annotations

import os
import subprocess

import pytest

from app_server.service_client import ServiceOperationError
from app_server.service_state import ServiceFiles
from app_server.systemd_user import SystemdUserService


def test_systemd_registration_escapes_values_and_preserves_manual_lifetime(
    tmp_path, monkeypatch
):
    files = ServiceFiles(tmp_path / 'workspace $HOME % "quoted"' / "state.sqlite3")
    manager = SystemdUserService(files, directory=tmp_path / "units")
    calls = []

    def run(*args, **_kwargs):
        calls.append(args)
        output = (
            "ActiveState=inactive\nSubState=dead\nMainPID=0\n"
            if args and args[0] == "show" and manager.label in args
            else "255\n"
        )
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(manager, "_run", run)
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "private % home"))
    manager.install(port=3081, path='/tool $PATH/%/"bin"')
    configured = manager.read()
    assert configured["command"][4] == str(files.database)
    assert configured["environment"]["PATH"] == '/tool $PATH/%/"bin"'
    assert not any(args and args[0] == "start" for args in calls)
    assert "ExecStart=:" in manager.path.read_text()
    assert "Restart=on-failure" in manager.path.read_text()
    assert manager.path.stat().st_mode & 0o777 == 0o600
    manager.start(port=3081)
    assert ("start", manager.label) in calls
    assert not any("--system" in args for args in calls)
    manager.uninstall()
    assert not manager.path.exists()


def test_systemd_refuses_changed_command_or_active_registration(tmp_path, monkeypatch):
    manager = SystemdUserService(
        ServiceFiles(tmp_path / "state.sqlite3"), directory=tmp_path / "units"
    )
    monkeypatch.setattr(
        manager,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    manager.install(port=0)
    monkeypatch.setattr(manager, "job", lambda: {"loaded": True, "pid": os.getpid()})
    with pytest.raises(ServiceOperationError, match="Stop"):
        manager.install(port=1234)
    with pytest.raises(ServiceOperationError, match="Stop"):
        manager.uninstall()
    manager.path.write_text(manager.path.read_text() + "ExecStartPost=/bin/false\n")
    with pytest.raises(ServiceOperationError, match="configuration changed"):
        manager.read()


def test_systemd_without_user_bus_remains_explicit(tmp_path, monkeypatch):
    manager = SystemdUserService(
        ServiceFiles(tmp_path / "state.sqlite3"), directory=tmp_path / "units"
    )
    monkeypatch.setattr(
        manager,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0 if "enable" in args else 1, "", "no user bus"
        ),
    )
    manager.install(port=3081)
    with pytest.raises(ServiceOperationError, match="No user systemd session"):
        manager.start()
    assert manager.doctor()["sessionAvailable"] is False


def test_systemd_generated_unit_passes_the_real_linux_parser(tmp_path, monkeypatch):
    import shutil
    import sys
    from app_server.service_state import service_command, service_environment

    verifier = shutil.which("systemd-analyze")
    if sys.platform != "linux" or verifier is None:
        pytest.skip("requires the real Linux systemd unit parser")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    working = tmp_path / 'directory with % and "quotes"'
    working.mkdir()
    manager = SystemdUserService(
        ServiceFiles(tmp_path / "state.sqlite3"), directory=tmp_path
    )
    manager.path.write_text(
        manager._render(
            {
                "command": service_command(manager.files, 0),
                "directory": str(working),
                "environment": service_environment(),
                "port": 0,
            }
        )
    )
    result = subprocess.run(
        [verifier, "--user", "verify", str(manager.path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert manager.read()["directory"] == str(working)
