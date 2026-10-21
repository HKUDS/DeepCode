"""Manage the local service without opening another execution runtime."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from app_server.service_client import (
    ServiceClient,
    ServiceOperationError,
    ServiceUnavailable,
)
from app_server.service_state import (
    DEFAULT_PORT,
    EXIT_PORT_UNAVAILABLE,
    LAUNCH_INSTANCE_ENV,
    ServiceFiles,
    service_command,
    service_working_directory,
)
from core.file_lock import exclusive_file_lock
from core.persistence.database import default_database_path
from core.private_storage import open_existing_private_file


def _service_manager(files: ServiceFiles):
    if sys.platform == "darwin":
        from app_server.launchd import LaunchAgent

        return LaunchAgent(files)
    if sys.platform == "linux":
        from app_server.systemd_user import SystemdUserService

        return SystemdUserService(files)
    if sys.platform == "win32":
        from app_server.windows_task import WindowsUserTask

        return WindowsUserTask(files)
    return None


def start_service(
    files: ServiceFiles,
    *,
    port: int | None = None,
    timeout: float = 30.0,
    previous_port: int | None = None,
) -> dict:
    """``previous_port`` is where a stopped service listened; a detached one
    started without ``port`` comes back there instead of on the default."""
    with exclusive_file_lock(files.directory / "management.lock"):
        return _start_service(
            files, port=port, timeout=timeout, previous_port=previous_port
        )


def _start_service(
    files: ServiceFiles,
    *,
    port: int | None,
    timeout: float,
    previous_port: int | None = None,
) -> dict:
    if files.running():
        status = _wait_ready(
            ServiceClient(files), timeout, port=port, existing_only=True
        )
        if status is not None:
            return status
    agent = _service_manager(files)
    if agent is not None and agent.path.exists():
        agent.start(port=port)
        try:
            return _wait_ready(ServiceClient(files), timeout, port=port)
        except BaseException:
            agent.unload()
            raise
    # A detached service keeps its address; a service manager keeps its own.
    return _start_detached(
        files, port=previous_port if port is None else port, timeout=timeout
    )


def restart_service(
    files: ServiceFiles, *, port: int | None, timeout: float, cancel_running: bool
) -> dict:
    """Stop the service, then start it again from this installation."""
    with exclusive_file_lock(files.directory / "management.lock"):
        previous = files.read()
        agent = _service_manager(files)
        # A detached service keeps its address; a service manager keeps its own.
        if (
            port is None
            and previous is not None
            and not (agent and agent.path.exists())
        ):
            port = previous[0].port
        _stop_service(files, timeout=timeout, cancel_running=cancel_running)
        return _start_service(files, port=port, timeout=30)


def stop_service(files: ServiceFiles, *, timeout: float, cancel_running: bool) -> dict:
    with exclusive_file_lock(files.directory / "management.lock"):
        return _stop_service(files, timeout=timeout, cancel_running=cancel_running)


def _stop_service(files: ServiceFiles, *, timeout: float, cancel_running: bool) -> dict:
    agent = _service_manager(files)
    job = agent.job() if agent is not None else {"loaded": False}
    if job["loaded"]:
        discovered = files.read() if files.running() else None
        if files.running() and discovered is None:
            raise ServiceUnavailable("Service is starting; retry stop when it is ready")
        record = discovered[0] if discovered is not None else None
        client = ServiceClient(files)
        if record is not None:
            if job["pid"] not in (None, record.pid):
                raise ServiceOperationError(
                    "Service manager PID does not match the running service; inspect service doctor"
                )
            if not cancel_running:
                client.call(
                    "drain",
                    {"timeout": timeout},
                    timeout=timeout + 10,
                    instance_id=record.instance_id,
                )
        try:
            agent.unload()
        except BaseException:
            if record is not None and not cancel_running:
                client.call("resume", instance_id=record.instance_id)
            raise
        if record is not None:
            if job["pid"] is None:
                # An idle job may coexist with a manual service. Drain that
                # service before unloading too: launchd can start between queries.
                try:
                    client.call(
                        "stop",
                        {"timeout": timeout, "cancelRunning": cancel_running},
                        timeout=timeout + 10,
                        instance_id=record.instance_id,
                    )
                except (ServiceUnavailable, ServiceOperationError):
                    # A launchd child may already be shutting down after bootout.
                    # The bounded identity-aware wait below still verifies exit.
                    pass
            return _wait_stopped(files, record.instance_id, timeout=35)
    return _stop_detached(files, timeout=timeout, cancel_running=cancel_running)


def _start_detached(
    files: ServiceFiles, *, port: int | None = None, timeout: float = 30.0
) -> dict:
    client = ServiceClient(files)
    command = service_command(files, DEFAULT_PORT if port is None else port)
    instance_id = secrets.token_hex(16)
    process = subprocess.Popen(
        command,
        cwd=service_working_directory(command),
        env={**os.environ, LAUNCH_INSTANCE_ENV: instance_id},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        # CREATE_NO_WINDOW, not DETACHED_PROCESS. A detached service has no
        # console, so every console program it starts (the interpreter behind a
        # venv's python.exe, shells, MCP stdio servers) opens a visible console
        # window of its own, and closing that window can stop the service.
        # CREATE_NO_WINDOW gives the service a hidden console that those
        # programs share; the service still outlives the launcher.
        creationflags=(
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        if os.name == "nt"
        else 0,
    )
    try:
        result = _wait_ready(client, timeout, process=process, port=port)
        if result["instanceId"] != instance_id:
            # Another concurrent launcher won. Reap our losing child before
            # returning, so it cannot become a replacement after the winner stops.
            _stop_startup_child(process)
        return result
    except BaseException:
        _stop_startup_child(process)
        raise


def _stop_startup_child(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_ready(
    client: ServiceClient,
    timeout: float,
    *,
    process=None,
    port=None,
    existing_only=False,
) -> dict | None:
    deadline = time.monotonic() + timeout
    last_failure = "No service probe completed"
    while True:
        try:
            status = client.call("status", timeout=1)
            # The default port is a preference the service may have moved off.
            if (
                port not in (None, 0, DEFAULT_PORT)
                and status["url"] != f"http://127.0.0.1:{port}"
            ):
                raise ServiceOperationError(
                    f"Service already runs at {status['url']}; stop it before changing ports"
                )
            if status["phase"] == "drained":
                client.call("resume", instance_id=status["instanceId"])
                continue
            if status["phase"] == "ready":
                return status
        except ServiceUnavailable as exc:
            last_failure = str(exc)
        # A status probe can hold the lifetime lock briefly. If that apparent
        # owner exits, the caller must launch instead of waiting for a phantom.
        if existing_only and not client.files.running():
            return None
        if (
            process is not None
            and process.poll() is not None
            and not client.files.running()
        ):
            if process.returncode == EXIT_PORT_UNAVAILABLE:
                raise ServiceUnavailable(
                    f"Port {port} is in use or reserved by another program; "
                    "choose another --port"
                )
            raise ServiceUnavailable(
                f"Service startup failed (exit {process.returncode}); inspect {client.files.log}"
            )
        if time.monotonic() >= deadline:
            raise ServiceUnavailable(
                f"Service did not become ready; inspect {client.files.log}. "
                f"Last check: {last_failure}"
            )
        time.sleep(0.1)


def _stop_detached(
    files: ServiceFiles, *, timeout: float, cancel_running: bool
) -> dict:
    if not files.running():
        return {"phase": "stopped"}
    stopped = ServiceClient(files).call(
        "stop",
        {"timeout": timeout, "cancelRunning": cancel_running},
        timeout=timeout + 10,
    )
    return _wait_stopped(files, stopped["instanceId"], timeout=15)


def _wait_stopped(files: ServiceFiles, instance_id: str, *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while files.running():
        current = files.read()
        if current is not None and current[0].instance_id != instance_id:
            raise ServiceOperationError(
                "The stopped service has been replaced by another instance; the replacement was not stopped"
            )
        if time.monotonic() >= deadline:
            raise ServiceUnavailable(
                "Service accepted stop but cleanup has not finished; inspect service logs"
            )
        time.sleep(0.05)
    return {"phase": "stopped"}


def _logs(files: ServiceFiles, *, lines: int, follow: bool) -> None:
    offset = 0
    identity = None
    first = True
    while True:
        try:
            with os.fdopen(open_existing_private_file(files.log), "rb") as stream:
                info = os.fstat(stream.fileno())
                current = (info.st_dev, info.st_ino)
                if first:
                    stream.seek(max(0, info.st_size - 2 * 1024 * 1024))
                    content = stream.read().decode("utf-8", errors="replace")
                    print("\n".join(content.splitlines()[-lines:]), flush=True)
                else:
                    stream.seek(
                        0 if current != identity or info.st_size < offset else offset
                    )
                    print(
                        stream.read().decode("utf-8", errors="replace"),
                        end="",
                        flush=True,
                    )
                offset = stream.tell()
                identity = current
                first = False
        except FileNotFoundError:
            if not follow:
                print("No service log yet.")
        if not follow:
            return
        time.sleep(0.25)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode service",
        description="Start, inspect, and stop the local DeepCode background service.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "start",
        "status",
        "stop",
        "restart",
        "logs",
        "install",
        "uninstall",
        "doctor",
        "snapshot",
        "prepare-upgrade",
        "restore",
    ):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path)
        command.add_argument("--json", action="store_true")
        if name in {"start", "restart", "install"}:
            command.add_argument(
                "--port",
                type=int,
                help=f"Listen on this port (default {DEFAULT_PORT}, or a free port "
                f"while another program holds {DEFAULT_PORT})",
            )
        if name in {"snapshot", "prepare-upgrade"}:
            command.add_argument("--output", type=Path, required=True)
        if name in {"snapshot", "prepare-upgrade", "restore"}:
            command.add_argument(
                "--sessions",
                type=Path,
                help="Canonical Session directory for a service without saved layout metadata",
            )
        if name == "restore":
            command.add_argument("--snapshot", type=Path, required=True)
            command.add_argument(
                "--replace-data",
                action="store_true",
                help="Explicitly replace runtime data with the snapshot; project files are not restored",
            )
        if name in {"stop", "restart", "uninstall", "prepare-upgrade"}:
            mode = command.add_mutually_exclusive_group()
            mode.add_argument(
                "--drain",
                action="store_true",
                help="Wait for running tasks and terminals (default)",
            )
            mode.add_argument(
                "--cancel-running",
                action="store_true",
                help="Cancel current service work before stopping",
            )
            command.add_argument("--timeout", type=float, default=60.0)
        if name == "install":
            command.add_argument(
                "--at-login",
                action="store_true",
                required=True,
                help="Opt in to starting after operating-system user login",
            )
            command.add_argument(
                "--path",
                help="PATH for tools in the managed service (default: current PATH)",
            )
        if name == "logs":
            command.add_argument("--lines", type=int, default=100)
            command.add_argument("--follow", action="store_true")
    args = parser.parse_args(argv)
    if getattr(args, "port", None) is not None and not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if hasattr(args, "timeout") and not 0 <= args.timeout <= 300:
        parser.error("timeout must be between 0 and 300 seconds")
    files = ServiceFiles(args.database or default_database_path())
    try:
        if args.command in {"snapshot", "prepare-upgrade", "restore"}:
            from app_server.state_backup import (
                StatePaths,
                create_snapshot,
                restore_snapshot,
            )

            paths = StatePaths.current(files, sessions=args.sessions)
            if args.command == "prepare-upgrade":
                stop_service(
                    files, timeout=args.timeout, cancel_running=args.cancel_running
                )
            result = (
                restore_snapshot(paths, args.snapshot, replace_data=args.replace_data)
                if args.command == "restore"
                else create_snapshot(paths, args.output)
            )
            print(
                json.dumps(result, ensure_ascii=False, indent=None if args.json else 2)
            )
            return 0
        if args.command == "logs":
            if not 1 <= args.lines <= 10_000:
                parser.error("lines must be between 1 and 10000")
            _logs(files, lines=args.lines, follow=args.follow)
            return 0
        if args.command in {"install", "uninstall", "doctor"}:
            agent = _service_manager(files)
            if agent is None:
                raise ServiceOperationError(
                    "No user service manager is available on this platform"
                )
            with exclusive_file_lock(files.directory / "management.lock"):
                if args.command == "install":
                    result = agent.install(
                        port=DEFAULT_PORT if args.port is None else args.port,
                        path=args.path,
                    )
                elif args.command == "uninstall":
                    if agent.job()["loaded"]:
                        _stop_service(
                            files,
                            timeout=args.timeout,
                            cancel_running=args.cancel_running,
                        )
                    result = agent.uninstall()
                else:
                    result = agent.doctor()
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(
                    f"{agent.name}: {'installed' if result['installed'] else 'not installed'}"
                )
                print(f"File: {result['path']}")
                if args.command == "install":
                    print(
                        "Starts with the user session. Run 'deepcode service start' to start now."
                    )
                if args.command == "doctor":
                    for check in result["checks"]:
                        print(
                            f"  {check['name']}: {'ok' if check['ok'] else 'needs attention'}"
                        )
                    print(
                        f"User session available: {result.get('sessionAvailable', result.get('guiSessionAvailable', False))} · job loaded: {result['loaded']}"
                    )
                    if result["shellOnlyVariables"]:
                        print(
                            "Shell-only variables are not copied into the service manager: "
                            + ", ".join(result["shellOnlyVariables"])
                        )
            return (
                1
                if args.command == "doctor"
                and any(not check["ok"] for check in result["checks"])
                else 0
            )
        if args.command == "status":
            result = (
                ServiceClient(files).call("status")
                if files.running()
                else {"phase": "stopped"}
            )
            agent = _service_manager(files)
            if agent is not None:
                result["supervision"] = {
                    "installed": agent.path.exists(),
                    **agent.job(),
                }
        elif args.command == "stop":
            result = stop_service(
                files, timeout=args.timeout, cancel_running=args.cancel_running
            )
        elif args.command == "restart":
            result = restart_service(
                files,
                port=args.port,
                timeout=args.timeout,
                cancel_running=args.cancel_running,
            )
        else:
            result = start_service(files, port=args.port, timeout=30)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"DeepCode service: {result['phase']}")
            if "url" in result:
                print(f"Management endpoint: {result['url']} · PID {result['pid']}")
                print(
                    f"Active turns: {result['activeTurns']} · queued: {result['queuedTurns']} · terminals: {result['terminals']}"
                )
        return 0
    except KeyboardInterrupt:
        return 130
    except (ServiceUnavailable, ServiceOperationError, OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
