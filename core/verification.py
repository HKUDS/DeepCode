"""Frontend-neutral, allowlisted test discovery and bounded process execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_VERIFICATION_OUTPUT = 64 * 1024
MAX_DISCOVERY_FILES = 256
MAX_DISCOVERY_CHARS = 64 * 1024


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    id: str
    label: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerificationResult:
    command: VerificationCommand
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    output_truncated: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def discover_verification_commands(root: Path) -> tuple[VerificationCommand, ...]:
    commands: list[VerificationCommand] = []
    python_tests = _discover_python_tests(root)
    if python_tests == "pytest":
        commands.append(
            VerificationCommand(
                "pytest", "Python tests", ("python3", "-m", "pytest", "-q")
            )
        )
    elif python_tests == "unittest":
        commands.append(
            VerificationCommand(
                "unittest",
                "Python unittest",
                ("python3", "-m", "unittest", "discover", "-v"),
            )
        )
    if _has_npm_test_script(root / "package.json"):
        commands.append(VerificationCommand("npm-test", "Node tests", ("npm", "test")))
    if (root / "Cargo.toml").is_file():
        commands.append(
            VerificationCommand("cargo-test", "Rust tests", ("cargo", "test"))
        )
    return tuple(commands)


def _discover_python_tests(root: Path) -> str | None:
    """Choose one bounded, deterministic Python test runner for ``root``.

    Explicit project markers retain the existing pytest behavior. Small
    script-style projects often keep ``test_*.py`` beside the implementation
    and depend only on the standard library, so detect an actual ``unittest``
    import before falling back to pytest for those root-level files.
    """
    if any(
        (root / name).exists()
        for name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tests")
    ):
        return "pytest"

    test_files = sorted(
        {
            *root.glob("test_*.py"),
            *root.glob("*_test.py"),
        }
    )
    if not test_files:
        return None
    for path in test_files[:MAX_DISCOVERY_FILES]:
        try:
            with path.open(encoding="utf-8") as handle:
                source = handle.read(MAX_DISCOVERY_CHARS)
        except (OSError, UnicodeDecodeError):
            continue
        if "import unittest" in source or "from unittest" in source:
            return "unittest"
    return "pytest"


def run_verification(
    root: Path,
    command: VerificationCommand,
    *,
    timeout_seconds: int = 300,
) -> VerificationResult:
    started = time.monotonic()
    process = subprocess.Popen(
        command.argv,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "CI": "1", "PYTHONUNBUFFERED": "1"},
        start_new_session=os.name != "nt",
    )
    stdout_raw, stderr_raw, timed_out, truncated = _capture(process, timeout_seconds)
    return VerificationResult(
        command=command,
        exit_code=process.returncode,
        timed_out=timed_out,
        duration_ms=round((time.monotonic() - started) * 1000),
        stdout=_bounded_text(stdout_raw),
        stderr=_bounded_text(stderr_raw),
        output_truncated=truncated,
    )


def _capture(
    process: subprocess.Popen[bytes], timeout_seconds: int
) -> tuple[bytes, bytes, bool, bool]:
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    truncated = threading.Event()

    def drain_tail(stream, target: bytearray) -> None:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            target.extend(chunk)
            if len(target) > MAX_VERIFICATION_OUTPUT:
                del target[: len(target) - MAX_VERIFICATION_OUTPUT]
                truncated.set()

    stdout_thread = threading.Thread(
        target=drain_tail, args=(process.stdout, stdout), daemon=True
    )
    stderr_thread = threading.Thread(
        target=drain_tail, args=(process.stderr, stderr), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)
        process.wait()
    finally:
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
    return bytes(stdout), bytes(stderr), timed_out, truncated.is_set()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.75)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _bounded_text(value: bytes) -> str:
    return value[-MAX_VERIFICATION_OUTPUT:].decode("utf-8", errors="replace")


def _has_npm_test_script(package_json: Path) -> bool:
    if not package_json.is_file():
        return False
    try:
        document = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    scripts = document.get("scripts")
    if not isinstance(scripts, dict) or not isinstance(scripts.get("test"), str):
        return False
    test_script = scripts["test"].strip()
    return bool(test_script) and "no test specified" not in test_script.lower()
