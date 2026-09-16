"""Linux user-systemd supervision for the existing service process."""

from __future__ import annotations

import configparser
import hashlib
import os
import shlex
import subprocess
from pathlib import Path

from app_server.service_client import ServiceOperationError
from app_server.service_state import (
    ServiceFiles,
    service_command,
    service_command_port,
    service_environment,
    service_working_directory,
    shell_only_variables,
)
from core.config import deepcode_home
from core.private_storage import open_existing_private_file, open_private_file


def _specifier(value: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise ValueError(
            "Service paths and environment cannot contain control characters"
        )
    return value.replace("%", "%%")


def _quote(value: str) -> str:
    return '"' + _specifier(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class SystemdUserService:
    name = "systemd user service"

    def __init__(self, files: ServiceFiles, *, directory: Path | None = None):
        self.files = files
        identity = hashlib.sha256(str(files.database).encode()).hexdigest()[:16]
        self.label = f"deepcode-{identity}.service"
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        if not config.is_absolute():
            raise ValueError("XDG_CONFIG_HOME must be an absolute path")
        self.path = (directory or config / "systemd/user") / self.label

    @staticmethod
    def _run(*args, timeout=15):
        try:
            return subprocess.run(
                ["systemctl", "--user", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceOperationError(
                "Cannot communicate with the user systemd manager; inspect service doctor"
            ) from exc

    def available(self):
        try:
            return self._run("show", "--property=Version", "--value").returncode == 0
        except ServiceOperationError:
            return False

    def job(self):
        if not self.path.exists() or not self.available():
            return {"loaded": False, "pid": None}
        result = self._run(
            "show", self.label, "--property=ActiveState,SubState,MainPID", "--no-pager"
        )
        if result.returncode:
            raise ServiceOperationError("Cannot inspect the installed systemd service")
        values = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        active = values.get("ActiveState") in {
            "active",
            "activating",
            "reloading",
            "deactivating",
        }
        pid = int(values.get("MainPID", "0"))
        return {"loaded": active, "pid": pid or None}

    def read(self):
        try:
            with os.fdopen(
                open_existing_private_file(self.path), "r", encoding="utf-8"
            ) as stream:
                text = stream.read(65537)
        except FileNotFoundError:
            return None
        if len(text) > 65536:
            raise ServiceOperationError("Systemd service file is too large")
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        try:
            parser.read_string(text)
            command = parser["Service"]["ExecStart"]
            if not command.startswith(":"):
                raise ValueError("Environment substitution must be disabled")
            arguments = [value.replace("%%", "%") for value in shlex.split(command[1:])]
            port = service_command_port(arguments, self.files.database)
            directory = parser["Service"]["WorkingDirectory"].replace("%%", "%")
            environment = dict(
                value.replace("%%", "%").split("=", 1)
                for value in shlex.split(parser["Service"]["Environment"])
            )
            if (
                not Path(directory).is_absolute()
                or parser["Service"]["Restart"] != "on-failure"
                or parser["Service"]["KillMode"] != "mixed"
                or set(environment) - {"DEEPCODE_HOME", "DEEPCODE_SESSIONS_DIR", "PATH"}
            ):
                raise ValueError("Unexpected service configuration")
            value = {
                "command": arguments,
                "directory": directory,
                "environment": environment,
                "port": port,
            }
            if text != self._render(value):
                raise ValueError(
                    "Service definition differs from its managed configuration"
                )
            return value
        except (configparser.Error, KeyError, ValueError, IndexError) as exc:
            raise ServiceOperationError(
                "Systemd service configuration changed; stop and reinstall it"
            ) from exc

    @staticmethod
    def _render(value):
        return "\n".join(
            [
                "[Unit]",
                "Description=DeepCode local service",
                "StartLimitIntervalSec=120",
                "StartLimitBurst=5",
                "",
                "[Service]",
                "Type=exec",
                "ExecStart=:" + " ".join(_quote(arg) for arg in value["command"]),
                "WorkingDirectory=" + _specifier(value["directory"]),
                "Environment="
                + " ".join(
                    _quote(key + "=" + item)
                    for key, item in value["environment"].items()
                ),
                "Restart=on-failure",
                "RestartSec=10",
                "TimeoutStopSec=35",
                "KillMode=mixed",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )

    def install(self, *, port, path=None):
        command = service_command(self.files, port)
        service_command_port(command, self.files.database)
        environment = service_environment(path)
        value = {
            "command": command,
            "directory": str(service_working_directory(command)),
            "environment": environment,
            "port": port,
        }
        previous = self.read()
        if previous != value and self.job()["loaded"]:
            raise ServiceOperationError(
                "Stop the loaded service before changing its systemd unit"
            )
        if previous != value:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            try:
                fd = open_private_file(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(self._render(value))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        result = self._run("--no-reload", "enable", str(self.path))
        if result.returncode:
            raise ServiceOperationError(
                "Could not enable the user service; inspect service doctor"
            )
        if self.available() and self._run("daemon-reload").returncode:
            raise ServiceOperationError("Systemd could not reload the installed unit")
        return {"installed": True, "atLogin": True, "path": str(self.path)}

    def start(self, *, port=None):
        value = self.read()
        if value is None:
            raise ServiceOperationError("No user service is installed")
        if port is not None and port != value["port"]:
            raise ServiceOperationError(
                "Requested port differs from the installed service; reinstall it"
            )
        if not self.available():
            raise ServiceOperationError(
                "No user systemd session is available; use deepcode serve --foreground"
            )
        if (
            not Path(value["command"][0]).is_file()
            or not Path(value["directory"]).is_dir()
        ):
            raise ServiceOperationError(
                "Installed runtime path is missing; reinstall the service"
            )
        self._run("reset-failed", self.label)
        result = self._run("start", self.label)
        if result.returncode:
            raise ServiceOperationError(
                "Systemd could not start the service; inspect service doctor and journalctl --user"
            )

    def unload(self):
        if (
            self.job()["loaded"]
            and self._run("stop", self.label, timeout=50).returncode
        ):
            raise ServiceOperationError("Systemd could not stop the service")

    def uninstall(self):
        if self.job()["loaded"]:
            raise ServiceOperationError("Stop the user service before uninstalling it")
        value = self.read()
        if value is not None:
            if self._run("--no-reload", "disable", self.label).returncode:
                raise ServiceOperationError("Could not disable the user service")
            self.path.unlink()
            if self.available():
                self._run("daemon-reload")
        return {"installed": False, "atLogin": False, "path": str(self.path)}

    def doctor(self):
        available = self.available()
        checks = [{"name": "userManager", "ok": available}]
        try:
            value = self.read()
            checks.append({"name": "configuration", "ok": value is not None})
            if value:
                checks.extend(
                    [
                        {
                            "name": "executable",
                            "ok": Path(value["command"][0]).is_file(),
                        },
                        {
                            "name": "workingDirectory",
                            "ok": Path(value["directory"]).is_dir(),
                        },
                        {
                            "name": "runtimeHome",
                            "ok": value["environment"].get("DEEPCODE_HOME")
                            == str(deepcode_home()),
                        },
                    ]
                )
        except ServiceOperationError as exc:
            checks.append({"name": "configuration", "ok": False, "message": str(exc)})
        return {
            "installed": self.path.exists(),
            "path": str(self.path),
            "sessionAvailable": available,
            **self.job(),
            "checks": checks,
            "shellOnlyVariables": shell_only_variables(),
        }
