"""Create the isolated Python 3.12 environment used for Desktop packaging."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = DESKTOP_ROOT / "build" / "sidecar" / ".venv"
LOCK_PATH = DESKTOP_ROOT / "sidecar-requirements.lock"
LOCK_MARKER = ".sidecar-lock-sha256"


def _python_executable(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _find_python312() -> Path:
    configured = os.environ.get("DEEPCODE_SIDECAR_BOOTSTRAP_PYTHON")
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = shutil.which("python3.12")
    if candidate:
        return Path(candidate).resolve()
    current = Path(sys.executable).resolve()
    completed = subprocess.run(
        [
            str(current),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip() == "3.12":
        return current
    uv = shutil.which("uv")
    if uv:
        completed = subprocess.run(
            [uv, "python", "find", "3.12"],
            check=True,
            capture_output=True,
            text=True,
        )
        resolved = completed.stdout.strip()
        if resolved:
            return Path(resolved).resolve()
    raise RuntimeError(
        "Python 3.12 was not found. Install it or set "
        "DEEPCODE_SIDECAR_BOOTSTRAP_PYTHON."
    )


def _check_version(python: Path) -> None:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip() != "3.12":
        raise RuntimeError(f"sidecar packaging requires Python 3.12, got {python}")


def _is_python312(python: Path) -> bool:
    try:
        _check_version(python)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return False
    return True


def main() -> int:
    if not LOCK_PATH.is_file():
        raise RuntimeError(
            f"{LOCK_PATH} is missing; regenerate it from sidecar-requirements.in"
        )
    bootstrap = _find_python312()
    _check_version(bootstrap)
    fingerprint = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
    environment_python = _python_executable(ENV_ROOT)
    marker = ENV_ROOT / LOCK_MARKER
    if ENV_ROOT.exists() and (
        not environment_python.is_file()
        or not _is_python312(environment_python)
        or not marker.is_file()
        or marker.read_text(encoding="utf-8").strip() != fingerprint
    ):
        shutil.rmtree(ENV_ROOT)
        environment_python = _python_executable(ENV_ROOT)
    if not environment_python.is_file():
        ENV_ROOT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(bootstrap), "-m", "venv", str(ENV_ROOT)],
            check=True,
        )
    subprocess.run(
        [
            str(environment_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--requirement",
            str(LOCK_PATH),
        ],
        check=True,
    )
    marker.write_text(f"{fingerprint}\n", encoding="utf-8")
    print(environment_python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
