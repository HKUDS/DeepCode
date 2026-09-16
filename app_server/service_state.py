"""Private discovery records and the managed service's OS-backed lifetime lease."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from core.file_lock import FileLease, exclusive_file_lock
from core.private_storage import open_existing_private_file, open_private_file
from core.version import __version__

SERVICE_PROTOCOL_VERSION = 1
SERVICE_WORKING_DIRECTORY = Path(__file__).resolve().parents[1]


def service_command(files: ServiceFiles, port: int) -> list[str]:
    # Keep the venv executable path; resolving its symlink loses the venv.
    if getattr(sys, "frozen", False):
        from app_server.runtime_install import pinned_service_executable

        launcher = [str(pinned_service_executable()), "--serve"]
    else:
        launcher = [os.path.abspath(sys.executable), "-m", "app_server.service"]
    return [
        *launcher,
        "--database",
        str(files.database),
        "--port",
        str(port),
        "--log-file",
    ]


def identity_proof(token: str, instance_id: str, challenge: str) -> str:
    return hmac.new(
        token.encode(), f"{instance_id}:{challenge}".encode(), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class ServiceRecord:
    instance_id: str
    database: str
    pid: int
    port: int
    version: str = __version__
    protocol_version: int = SERVICE_PROTOCOL_VERSION

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class ServiceFiles:
    def __init__(self, database: Path) -> None:
        self.database = database.expanduser().resolve()
        self.directory = self.database.with_name(self.database.name + ".service")
        self.lock = self.directory / "instance.lock"
        self._discovery_lock = self.directory / "discovery.lock"
        self.record = self.directory / "instance.json"
        self.token = self.directory / "token"
        self.log = self.directory / "service.log"

    def acquire(self) -> FileLease | None:
        return FileLease.acquire(self.lock, shared=False, blocking=False)

    def running(self) -> bool:
        lease = self.acquire()
        if lease is None:
            return True
        lease.close()
        return False

    def read(self) -> tuple[ServiceRecord, str] | None:
        # The lifetime lease identifies the owner; it does not exclude readers.
        # Keep the record/token pair consistent and prevent Windows readers from
        # opening files while the owner deletes or replaces them.
        with exclusive_file_lock(self._discovery_lock):
            try:
                value = json.loads(_read(self.record))
                token = _read(self.token).strip()
            except FileNotFoundError:
                return None
        if not isinstance(value, dict):
            raise ValueError("Invalid service discovery record")
        try:
            record = ServiceRecord(**value)
        except TypeError as exc:
            raise ValueError("Invalid service discovery record") from exc
        if (
            record.database != str(self.database)
            or type(record.pid) is not int
            or record.pid < 1
            or type(record.port) is not int
            or not 1 <= record.port <= 65535
            or type(record.protocol_version) is not int
            or record.protocol_version != SERVICE_PROTOCOL_VERSION
            or not isinstance(record.version, str)
            or not isinstance(record.instance_id, str)
            or len(record.instance_id) != 32
            or any(char not in "0123456789abcdef" for char in record.instance_id)
            or len(token) != 64
            or any(char not in "0123456789abcdef" for char in token)
        ):
            raise ValueError("Invalid or incompatible service discovery record")
        return record, token

    def publish(self, record: ServiceRecord, token: str) -> None:
        with exclusive_file_lock(self._discovery_lock):
            _write(self.token, token)
            _write(self.record, json.dumps(asdict(record)))

    def clear(self) -> None:
        """Only the exclusive lease holder may remove these records."""
        with exclusive_file_lock(self._discovery_lock):
            self.record.unlink(missing_ok=True)
            self.token.unlink(missing_ok=True)


def _read(path: Path) -> str:
    with os.fdopen(open_existing_private_file(path), "r", encoding="utf-8") as stream:
        data = stream.read(16_385)
    if len(data) > 16_384:
        raise ValueError("Service discovery record is too large")
    return data


def _write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    try:
        fd = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def service_command_port(arguments: list[str], database: Path) -> int:
    """Validate either source or frozen launch syntax without executing it."""
    if not isinstance(arguments, list) or not all(
        isinstance(value, str) for value in arguments
    ):
        raise ValueError("Invalid service arguments")
    if arguments[1:3] == ["-m", "app_server.service"]:
        tail = arguments[3:]
    elif arguments[1:2] == ["--serve"]:
        tail = arguments[2:]
    else:
        raise ValueError("Unsupported service launcher")
    if (
        len(tail) != 5
        or tail[:2] != ["--database", str(database)]
        or tail[2] != "--port"
        or tail[4] != "--log-file"
        or not tail[3].isdigit()
        or not 0 <= int(tail[3]) <= 65535
        or not Path(arguments[0]).is_absolute()
    ):
        raise ValueError("Invalid service launch identity")
    return int(tail[3])


def service_working_directory(arguments: list[str]) -> Path:
    return (
        Path(arguments[0]).parent
        if arguments[1:2] == ["--serve"]
        else SERVICE_WORKING_DIRECTORY
    )


def service_environment(path: str | None = None) -> dict[str, str]:
    """Capture only the deliberate launch environment, never shell credentials."""
    from core.config import deepcode_home

    environment = {
        "DEEPCODE_HOME": str(deepcode_home()),
        "PATH": path if path is not None else os.environ.get("PATH", os.defpath),
    }
    if os.environ.get("DEEPCODE_SESSIONS_DIR"):
        environment["DEEPCODE_SESSIONS_DIR"] = str(
            Path(os.environ["DEEPCODE_SESSIONS_DIR"]).expanduser().resolve()
        )
    return environment


def shell_only_variables() -> list[str]:
    from core.providers.registry import PROVIDERS

    names = {provider.env_key for provider in PROVIDERS if provider.env_key}
    names.update(
        key
        for key in os.environ
        if key.endswith("_API_KEY")
        or key.lower() in {"http_proxy", "https_proxy", "all_proxy"}
    )
    return sorted(name for name in names if os.environ.get(name))
