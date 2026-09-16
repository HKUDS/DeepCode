"""macOS user LaunchAgents for the existing DeepCode service executable."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import subprocess
from pathlib import Path
from xml.parsers.expat import ExpatError

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


class LaunchAgent:
    """OS adapter; the service CLI owns management locking and task draining."""

    name = "LaunchAgent"

    def __init__(self, files: ServiceFiles, *, directory: Path | None = None) -> None:
        self.files = files
        identity = hashlib.sha256(str(files.database).encode()).hexdigest()[:16]
        self.label = f"ai.deepcode.service.{identity}"
        self.domain = f"gui/{os.getuid()}"
        self.target = f"{self.domain}/{self.label}"
        self.path = (
            directory or Path.home() / "Library" / "LaunchAgents"
        ) / f"{self.label}.plist"

    @staticmethod
    def _run(*args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["/bin/launchctl", *args], capture_output=True, text=True, timeout=15
            )
        except subprocess.TimeoutExpired as exc:
            raise ServiceOperationError(
                "launchctl did not finish; inspect service doctor before retrying"
            ) from exc

    def available(self) -> bool:
        return self._run("print", self.domain).returncode == 0

    def job(self) -> dict:
        result = self._run("print", self.target)
        if result.returncode:
            if (
                "Could not find service" in result.stderr
                or "Could not find domain" in result.stderr
            ):
                return {"loaded": False, "pid": None}
            raise ServiceOperationError(
                f"Cannot inspect LaunchAgent: {result.stderr.strip()}"
            )
        pid = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
        return {"loaded": True, "pid": int(pid[1]) if pid else None}

    def read(self) -> dict | None:
        try:
            with os.fdopen(open_existing_private_file(self.path), "rb") as stream:
                data = stream.read(65_537)
        except FileNotFoundError:
            return None
        if len(data) > 65_536:
            raise ServiceOperationError("LaunchAgent file is too large")
        try:
            value = plistlib.loads(data)
        except (ValueError, plistlib.InvalidFileException, ExpatError) as exc:
            raise ServiceOperationError("Invalid DeepCode LaunchAgent plist") from exc
        if not isinstance(value, dict) or value.get("Label") != self.label:
            raise ServiceOperationError(
                "LaunchAgent identity does not match this database"
            )
        arguments = value.get("ProgramArguments")
        try:
            service_command_port(arguments, self.files.database)
        except ValueError as exc:
            raise ServiceOperationError(
                "LaunchAgent command has changed; stop and reinstall it"
            ) from exc
        if (
            not isinstance(value.get("WorkingDirectory"), str)
            or not Path(value["WorkingDirectory"]).is_absolute()
            or value.get("KeepAlive") != {"SuccessfulExit": False}
            or value.get("RunAtLoad") is not True
            or not isinstance(value.get("EnvironmentVariables"), dict)
        ):
            raise ServiceOperationError(
                "LaunchAgent configuration has changed; stop and reinstall it"
            )
        return value

    def install(self, *, port: int, path: str | None = None) -> dict:
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        environment = service_environment(path)
        command = service_command(self.files, port)
        value = {
            "Label": self.label,
            "ProgramArguments": command,
            "WorkingDirectory": str(service_working_directory(command)),
            "EnvironmentVariables": environment,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "ExitTimeOut": 30,
            "ProcessType": "Background",
        }
        previous = self.read()
        if previous != value and self.job()["loaded"]:
            raise ServiceOperationError(
                "Stop the loaded service before updating its LaunchAgent"
            )
        if previous != value:
            # LaunchAgents is a shared user directory; do not chmod it as though
            # DeepCode owned every agent in it. Only our plist is private.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".plist.tmp")
            try:
                fd = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                with os.fdopen(fd, "wb") as stream:
                    plistlib.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"installed": True, "atLogin": True, "path": str(self.path)}

    def start(self, *, port: int | None = None) -> None:
        value = self.read()
        if value is None:
            raise ServiceOperationError("No LaunchAgent installed")
        if port is not None and port != service_command_port(
            value["ProgramArguments"], self.files.database
        ):
            raise ServiceOperationError(
                "Requested port differs from the installed LaunchAgent; reinstall with the new port"
            )
        if not self.available():
            raise ServiceOperationError(
                "No macOS desktop login session is available; use deepcode serve --foreground"
            )
        for key in ("ProgramArguments", "WorkingDirectory"):
            target = value[key][0] if key == "ProgramArguments" else value[key]
            if not Path(target).exists():
                raise ServiceOperationError(
                    f"Installed service path is missing: {target}; reinstall the LaunchAgent"
                )
        if self.job()["loaded"]:
            args = ("kickstart", self.target)
        else:
            args = ("bootstrap", self.domain, str(self.path))
        result = self._run(*args)
        if result.returncode:
            raise ServiceOperationError(
                f"Cannot start LaunchAgent: {result.stderr.strip()}"
            )

    def unload(self) -> None:
        if not self.job()["loaded"]:
            return
        result = self._run("bootout", self.target)
        if result.returncode and self.job()["loaded"]:
            raise ServiceOperationError(
                f"Cannot unload LaunchAgent: {result.stderr.strip()}"
            )

    def uninstall(self) -> dict:
        if self.job()["loaded"]:
            raise ServiceOperationError("Stop the LaunchAgent before uninstalling it")
        self.read()  # Refuse to remove an unrelated/corrupt entry.
        self.path.unlink(missing_ok=True)
        return {"installed": False, "atLogin": False, "path": str(self.path)}

    def doctor(self) -> dict:
        checks = []
        try:
            value = self.read()
        except ServiceOperationError as exc:
            value = None
            checks.append({"name": "configuration", "ok": False, "message": str(exc)})
        job = self.job()
        if value:
            executable = value["ProgramArguments"][0]
            directory = value["WorkingDirectory"]
            checks.extend(
                [
                    {
                        "name": "executable",
                        "ok": os.path.isfile(executable)
                        and os.access(executable, os.X_OK),
                        "path": executable,
                    },
                    {
                        "name": "workingDirectory",
                        "ok": Path(directory).is_dir(),
                        "path": directory,
                    },
                    {
                        "name": "runtimeHome",
                        "ok": value["EnvironmentVariables"].get("DEEPCODE_HOME")
                        == str(deepcode_home()),
                    },
                ]
            )
        return {
            "platform": "macos",
            "installed": self.path.exists(),
            "atLogin": value is not None,
            "path": str(self.path),
            "guiSessionAvailable": self.available(),
            **job,
            "checks": checks,
            "shellOnlyVariables": shell_only_variables(),
        }
