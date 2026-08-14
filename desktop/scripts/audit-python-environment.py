"""Audit an existing Python environment without installing tools into it."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _site_packages(python: Path) -> Path:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    path = Path(completed.stdout.strip())
    if not path.is_dir():
        raise RuntimeError(f"target site-packages directory is missing: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Do not resolve symlinks here. A virtualenv's python executable commonly
    # points at its base interpreter; dereferencing it loses the virtualenv
    # prefix and audits the wrong site-packages directory.
    python = Path(os.path.abspath(args.python.expanduser()))
    if not python.is_file():
        raise RuntimeError(f"target Python executable is missing: {python}")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deepcode-pip-audit-") as cache:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--path",
                str(_site_packages(python)),
                "--cache-dir",
                cache,
                "--format",
                "cyclonedx-json",
                "--output",
                str(output),
            ],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
