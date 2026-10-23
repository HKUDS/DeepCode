"""``deepcode feishu`` — run the Feishu approval bridge.

The bridge itself lives in :mod:`integrations.feishu_approver`; this module is
only the CLI surface so the integration has a discoverable, documented entry
point instead of a bare ``python -m`` invocation.  The heavy ``lark_oapi``
import stays inside :func:`integrations.feishu_approver.__main__.main` so a
configuration error (or ``--help``) never pays the SDK import cost.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _pid_file() -> Path:
    """Where the detached bridge records its pid.

    Fixed location derived from ``DEEPCODE_HOME`` (falling back to
    ``~/.deepcode``), never the process cwd — a bridge spawned from another
    working directory must still be visible to ``status``/``stop``.
    """
    home = os.environ.get("DEEPCODE_HOME") or str(Path.home() / ".deepcode")
    return Path(home) / "feishu-approver.pid"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepcode feishu",
        description="Run the Feishu approval bridge.",
    )
    commands = parser.add_subparsers(dest="command")

    start = commands.add_parser("start", help="Run the bridge in the foreground.")
    start.add_argument(
        "--detach",
        action="store_true",
        help="Spawn the bridge in the background and return immediately.",
    )

    commands.add_parser("status", help="Report whether a detached bridge is running.")
    commands.add_parser("stop", help="Stop a bridge started with --detach.")
    return parser


def _pid_alive(pid: int) -> bool:
    """True when *pid* names a live process (POSIX and Windows)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Windows raises OSError for "exists but not ours"; anything other
        # than a clean lookup is treated as running so stop() can still try.
        return True
    return True


def _read_pid() -> int | None:
    try:
        return int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    path = _pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    try:
        _pid_file().unlink()
    except OSError:
        pass


def _run_foreground() -> int:
    # Deferred so `--help` and a config error stay lark_oapi-free.
    from integrations.feishu_approver.__main__ import main as bridge_main

    return bridge_main()


def _start_detached() -> int:
    existing = _read_pid()
    if existing is not None and _pid_alive(existing):
        print(f"feishu bridge already running (pid {existing})", file=sys.stderr)
        return 1

    # Run from the repo root (same convention as the app_server service) so
    # ``-m integrations.feishu_approver`` resolves the package regardless of
    # the caller's cwd.
    from app_server.service_state import SERVICE_WORKING_DIRECTORY

    # The bridge already mirrors its logs to ``FEISHU_APPROVER_LOG``; capture
    # the detached stdio there too (else a ``pythonw``/detached run drops the
    # startup/config-error output into a black hole).
    log_path = os.environ.get("FEISHU_APPROVER_LOG", "").strip()
    stdout_target = subprocess.DEVNULL
    log_handle = None
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "ab")  # noqa: SIM115
        stdout_target = log_handle
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "integrations.feishu_approver"],
            stdout=stdout_target,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(SERVICE_WORKING_DIRECTORY),
            close_fds=True,
        )
    finally:
        if log_handle is not None:
            log_handle.close()
    _write_pid(proc.pid)
    print(f"feishu bridge started in background (pid {proc.pid})")
    return 0


def _status() -> int:
    pid = _read_pid()
    if pid is None:
        print(json.dumps({"running": False, "reason": "no pid file"}))
        return 3
    if _pid_alive(pid):
        print(json.dumps({"running": True, "pid": pid}))
        return 0
    print(json.dumps({"running": False, "pid": pid, "reason": "stale pid file"}))
    _clear_pid()
    return 3


def _stop() -> int:
    pid = _read_pid()
    if pid is None or not _pid_alive(pid):
        print("feishu bridge is not running", file=sys.stderr)
        _clear_pid()
        return 3
    try:
        os.kill(pid, 15)  # SIGTERM; on Windows this is a best-effort terminate.
    except OSError as exc:
        print(f"failed to signal pid {pid}: {exc}", file=sys.stderr)
        return 1
    _clear_pid()
    print(f"feishu bridge stopped (pid {pid})")
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "start":
        if args.detach:
            return _start_detached()
        return _run_foreground()
    if args.command == "status":
        return _status()
    if args.command == "stop":
        return _stop()
    _parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
