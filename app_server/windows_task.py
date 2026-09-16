"""User Task Scheduler adapter; no administrator service or shell wrapper."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app_server.managed_entry import read_configuration
from app_server.service_client import ServiceClient, ServiceOperationError
from app_server.service_state import (
    ServiceFiles,
    service_command,
    service_command_port,
    service_environment,
    service_working_directory,
    shell_only_variables,
)
from core.private_storage import open_private_file

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", TASK_NS)


def task_definition(config: dict, sid: str, label: str, configuration: Path) -> str:
    def child(parent, name, text=None, **attributes):
        element = ET.SubElement(parent, "{" + TASK_NS + "}" + name, attributes)
        element.text = text
        return element

    root = ET.Element("{" + TASK_NS + "}Task", {"version": "1.4"})
    registration = child(root, "RegistrationInfo")
    child(registration, "Description", "DeepCode local service " + label)
    child(registration, "URI", "\\DeepCode\\" + label)
    trigger = child(child(root, "Triggers"), "LogonTrigger")
    child(trigger, "Enabled", "true")
    child(trigger, "UserId", sid)
    principal = child(child(root, "Principals"), "Principal", id="Author")
    child(principal, "UserId", sid)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(root, "Settings")
    for name, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "AllowHardTerminate": "true",
        "StartWhenAvailable": "true",
        "RunOnlyIfNetworkAvailable": "false",
        "AllowStartOnDemand": "true",
        "Enabled": "true",
        "Hidden": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        child(settings, name, value)
    restart = child(settings, "RestartOnFailure")
    child(restart, "Interval", "PT1M")
    child(restart, "Count", "3")
    action = child(child(root, "Actions", Context="Author"), "Exec")
    arguments = config["command"]
    frozen = arguments[1:2] == ["--serve"]
    program = Path(arguments[0])
    if not frozen and program.with_name("pythonw.exe").is_file():
        program = program.with_name("pythonw.exe")
    child(action, "Command", str(program))
    child(
        action,
        "Arguments",
        subprocess.list2cmdline(
            ([] if frozen else ["-m", "app_server"])
            + ["--managed-config", str(configuration)]
        ),
    )
    child(action, "WorkingDirectory", config["directory"])
    return ET.tostring(root, encoding="unicode")


class WindowsUserTask:
    name = "Windows user task"

    def __init__(self, files: ServiceFiles):
        self.files = files
        identity = hashlib.sha256(str(files.database).encode()).hexdigest()[:16]
        self.label = f"DeepCode-{identity}"
        self.path = files.directory / "task-configuration.json"

    def _run(self, script, *, timeout=20, extra=None):
        prelude = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        encoded = base64.b64encode((prelude + script).encode("utf-16-le")).decode(
            "ascii"
        )
        environment = {**os.environ, "DEEPCODE_TASK_LABEL": self.label, **(extra or {})}
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                check=False,
                capture_output=True,
                encoding="utf-8",
                timeout=timeout,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceOperationError(
                "Windows Task Scheduler operation did not finish; inspect service doctor"
            ) from exc
        if result.returncode:
            raise ServiceOperationError(
                "Windows Task Scheduler rejected the operation: "
                + result.stderr.strip()[:600]
            )
        return result.stdout.strip()

    @staticmethod
    def _connect():
        return (
            "$scheduler=New-Object -ComObject Schedule.Service; $scheduler.Connect(); "
        )

    def _sid(self):
        return self._run(
            "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"
        )

    def available(self):
        try:
            self._run(self._connect())
            return True
        except ServiceOperationError:
            return False

    def _task(self):
        script = (
            self._connect()
            + """
try { $folder=$scheduler.GetFolder('\\DeepCode'); $task=$folder.GetTask($env:DEEPCODE_TASK_LABEL) }
catch {
 $failure=$_.Exception
 while ($null -ne $failure) {
  if ($failure.HResult -in @(-2147024894,-2147024893)) { @{registered=$false} | ConvertTo-Json -Compress; exit 0 }
  $failure=$failure.InnerException
 }
 throw
}
@{registered=$true; state=[int]$task.State; xml=$task.Xml} | ConvertTo-Json -Compress
"""
        )
        value = json.loads(self._run(script))
        if value.get("registered"):
            root = ET.fromstring(value["xml"])
            ns = {"t": TASK_NS}
            if (
                root.findtext("t:RegistrationInfo/t:URI", namespaces=ns)
                != "\\DeepCode\\" + self.label
            ):
                raise ServiceOperationError(
                    "Task identity does not match this DeepCode service"
                )
            if (
                root.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns)
                != self._sid()
            ):
                raise ServiceOperationError("Task belongs to another Windows user")
        return value

    def _verify_definition(self, task, value):
        if not task.get("registered"):
            return
        actual = ET.fromstring(task["xml"])
        expected = ET.fromstring(
            task_definition(value, self._sid(), self.label, self.path)
        )
        ns = {"t": TASK_NS}
        for container in ("Actions", "Triggers", "Principals"):
            if len(actual.findall(f"t:{container}/*", ns)) != 1:
                raise ServiceOperationError(
                    "Task definition has unexpected actions or principals"
                )
        for path in (
            "Actions/Exec/Command",
            "Actions/Exec/Arguments",
            "Actions/Exec/WorkingDirectory",
            "Principals/Principal/LogonType",
            "Principals/Principal/RunLevel",
            "Triggers/LogonTrigger/UserId",
            "Settings/ExecutionTimeLimit",
            "Settings/MultipleInstancesPolicy",
            "Settings/RestartOnFailure/Count",
        ):
            query = "/".join("t:" + part for part in path.split("/"))
            if actual.findtext(query, namespaces=ns) != expected.findtext(
                query, namespaces=ns
            ):
                raise ServiceOperationError(
                    "Task definition changed; stop and reinstall it"
                )

    def job(self):
        if not self.path.exists():
            return {"loaded": False, "pid": None}
        task = self._task()
        return {"loaded": task.get("state") in {2, 4}, "pid": None}

    def read(self):
        try:
            value = read_configuration(self.path)
        except FileNotFoundError:
            return None
        try:
            if value["database"] != str(self.files.database):
                raise ValueError("Wrong service database")
            if (
                service_command_port(value["command"], self.files.database)
                != value["port"]
            ):
                raise ValueError("Wrong service port")
            if not Path(value["directory"]).is_absolute():
                raise ValueError("Invalid working directory")
        except (KeyError, ValueError) as exc:
            raise ServiceOperationError(
                "Task configuration changed; stop and reinstall it"
            ) from exc
        return value

    def install(self, *, port, path=None):
        command = service_command(self.files, port)
        service_command_port(command, self.files.database)
        environment = service_environment(path)
        value = {
            "schemaVersion": 1,
            "database": str(self.files.database),
            "port": port,
            "command": command,
            "directory": str(service_working_directory(command)),
            "environment": environment,
        }
        previous, task = self.read(), self._task()
        if previous is not None:
            self._verify_definition(task, previous)
        if task.get("registered") and previous is None:
            raise ServiceOperationError(
                "An existing task has no matching DeepCode configuration"
            )
        if previous != value and task.get("state") in {2, 4}:
            raise ServiceOperationError(
                "Stop the active task before updating its configuration"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        xml_path = self.path.with_suffix(".xml.tmp")
        try:
            with os.fdopen(
                open_private_file(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY),
                "w",
                encoding="utf-8",
            ) as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            definition = task_definition(value, self._sid(), self.label, self.path)
            with os.fdopen(
                open_private_file(xml_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write(definition)
            # Register only a logon trigger: registration does not launch work.
            script = (
                self._connect()
                + """
try { $folder=$scheduler.GetFolder('\\DeepCode') } catch { $folder=$scheduler.GetFolder('\\').CreateFolder('DeepCode') }
$definition=$scheduler.NewTask(0); $definition.XmlText=[IO.File]::ReadAllText($env:DEEPCODE_TASK_XML)
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$null=$folder.RegisterTaskDefinition($env:DEEPCODE_TASK_LABEL,$definition,6,$sid,$null,3)
"""
            )
            os.replace(temporary, self.path)
            try:
                self._run(script, extra={"DEEPCODE_TASK_XML": str(xml_path)})
            except BaseException:
                if previous is None:
                    self.path.unlink(missing_ok=True)
                else:
                    with os.fdopen(
                        open_private_file(
                            temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY
                        ),
                        "w",
                        encoding="utf-8",
                    ) as stream:
                        json.dump(previous, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                raise
        finally:
            temporary.unlink(missing_ok=True)
            xml_path.unlink(missing_ok=True)
        return {"installed": True, "atLogin": True, "path": str(self.path)}

    def start(self, *, port=None):
        value = self.read()
        task = self._task()
        if value is None or not task.get("registered"):
            raise ServiceOperationError("No Windows user task is installed")
        self._verify_definition(task, value)
        if port is not None and port != value["port"]:
            raise ServiceOperationError(
                "Requested port differs from the installed task"
            )
        self._run(
            self._connect()
            + "$task=$scheduler.GetFolder('\\DeepCode').GetTask($env:DEEPCODE_TASK_LABEL); $task.Enabled=$true; $null=$task.Run($null)"
        )

    def unload(self):
        if not self.job()["loaded"]:
            return
        task = "$task=$scheduler.GetFolder('\\DeepCode').GetTask($env:DEEPCODE_TASK_LABEL); "
        self._run(self._connect() + task + "$task.Enabled=$false")
        try:
            if self.files.running():
                stopped = ServiceClient(self.files).call(
                    "stop", {"timeout": 10, "cancelRunning": True}, timeout=25
                )
                deadline = time.monotonic() + 35
                while self.files.running():
                    current = self.files.read()
                    if (
                        current is not None
                        and current[0].instance_id != stopped["instanceId"]
                    ):
                        raise ServiceOperationError(
                            "Service was replaced during stop; the replacement was not stopped"
                        )
                    if time.monotonic() >= deadline:
                        raise ServiceOperationError(
                            "Service cleanup has not finished; inspect service logs"
                        )
                    time.sleep(0.05)
            self._run(self._connect() + task + "$task.Stop(0)")
        finally:
            # Ending while disabled suppresses failure restarts; restore only
            # the next-login setting, without issuing Run again.
            self._run(self._connect() + task + "$task.Enabled=$true")

    def uninstall(self):
        if self.job()["loaded"]:
            raise ServiceOperationError(
                "Stop the Windows user task before uninstalling it"
            )
        self.read()
        if self._task().get("registered"):
            self._run(
                self._connect()
                + "$scheduler.GetFolder('\\DeepCode').DeleteTask($env:DEEPCODE_TASK_LABEL,0)"
            )
        self.path.unlink(missing_ok=True)
        return {"installed": False, "atLogin": False, "path": str(self.path)}

    def doctor(self):
        available = self.available()
        checks = [{"name": "taskScheduler", "ok": available}]
        try:
            value = self.read()
            checks.append({"name": "configuration", "ok": value is not None})
            if value:
                checks.append(
                    {"name": "executable", "ok": Path(value["command"][0]).is_file()}
                )
        except (OSError, ValueError, ServiceOperationError) as exc:
            checks.append({"name": "configuration", "ok": False, "message": str(exc)})
        job = self.job() if available else {"loaded": False, "pid": None}
        return {
            "installed": self.path.exists(),
            "path": str(self.path),
            "sessionAvailable": available,
            **job,
            "checks": checks,
            "shellOnlyVariables": shell_only_variables(),
        }
