"""Read a private supervisor environment and enter the existing service host."""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.private_storage import open_existing_private_file

ENVIRONMENT_KEYS = frozenset({"DEEPCODE_HOME", "DEEPCODE_SESSIONS_DIR", "PATH"})


def read_configuration(path: Path) -> dict:
    with os.fdopen(open_existing_private_file(path), "r", encoding="utf-8") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Managed service configuration is too large")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or not isinstance(value.get("environment"), dict)
        or set(value["environment"]) - ENVIRONMENT_KEYS
        or any(
            not isinstance(item, str) or "\x00" in item
            for item in value["environment"].values()
        )
        or not isinstance(value.get("database"), str)
        or not Path(value["database"]).is_absolute()
        or type(value.get("port")) is not int
        or not 0 <= value["port"] <= 65535
    ):
        raise ValueError("Invalid managed service configuration")
    return value


def run(path: Path) -> int:
    value = read_configuration(path)
    from app_server.service_state import ServiceFiles

    if path.absolute().parent != ServiceFiles(Path(value["database"])).directory:
        raise ValueError("Supervisor configuration does not belong to this database")
    os.environ.update(value["environment"])
    from app_server.service import main

    return main(
        ["--database", value["database"], "--port", str(value["port"]), "--log-file"]
    )
