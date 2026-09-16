from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

import pytest

from app_server.managed_entry import read_configuration
from app_server.windows_task import TASK_NS, task_definition
from core.private_storage import open_private_file


def test_task_definition_has_user_scope_unlimited_duration_and_safe_arguments(tmp_path):
    configuration = tmp_path / 'directory with spaces & "quotes"' / "task.json"
    value = {
        "command": [str(tmp_path / "server.exe"), "--serve"],
        "directory": str(tmp_path),
    }
    root = ET.fromstring(
        task_definition(value, "S-1-5-21-123", "DeepCode-test", configuration)
    )
    ns = {"t": TASK_NS}
    assert (
        root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns)
        == "InteractiveToken"
    )
    assert (
        root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns)
        == "LeastPrivilege"
    )
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
    assert (
        root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns)
        == "IgnoreNew"
    )
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Count", namespaces=ns) == "3"
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
    assert command == str(tmp_path / "server.exe")
    assert arguments.startswith("--managed-config ")
    assert "cmd.exe" not in arguments and "powershell" not in arguments
    assert len(root.findall("t:Actions/*", ns)) == 1
    assert len(root.findall("t:Triggers/*", ns)) == 1


def test_supervisor_configuration_rejects_environment_injection(tmp_path):
    path = tmp_path / "configuration.json"
    value = {
        "schemaVersion": 1,
        "database": str(tmp_path / "state.sqlite3"),
        "port": 3081,
        "environment": {"DEEPCODE_HOME": str(tmp_path), "PATH": "/bin"},
    }
    with os.fdopen(open_private_file(path, os.O_CREAT | os.O_WRONLY), "w") as stream:
        json.dump(value, stream)
    assert read_configuration(path) == value
    value["environment"]["PYTHONPATH"] = "/untrusted"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Invalid managed"):
        read_configuration(path)
