"""Launch the native Desktop from an installed app or its source checkout."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname


def _is_checkout(root: Path) -> bool:
    return (root / "desktop/package.json").is_file() and (
        root / "desktop/src-tauri/tauri.conf.json"
    ).is_file()


def _source_checkout() -> Path | None:
    root = Path(__file__).resolve().parents[1]
    if _is_checkout(root):
        return root
    # A normal uv tool install from a local directory records its source. This
    # works without editable .pth hooks and without trusting the caller's cwd.
    try:
        data = json.loads(
            distribution("deepcode-hku").read_text("direct_url.json") or "{}"
        )
        url = urlsplit(data.get("url", ""))
        if url.scheme == "file" and url.netloc in ("", "localhost"):
            root = Path(url2pathname(url.path))
            if _is_checkout(root):
                return root
    except (PackageNotFoundError, ValueError, TypeError, OSError):
        pass
    return None


def _run_source(root: Path, *, setup: bool, options: list[str]) -> int:
    npm = shutil.which("npm")
    if npm is None:
        raise ValueError("Desktop source development requires Node.js 22+ and npm.")
    if shutil.which("cargo") is None:
        raise ValueError(
            "Desktop source development requires Rust and the platform Tauri prerequisites."
        )
    desktop = root / "desktop"
    python = (
        root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    environment = {
        **os.environ,
        "DEEPCODE_PYTHON": str(python if python.is_file() else sys.executable),
    }
    if (
        setup
        or not (
            desktop
            / "node_modules/.bin"
            / ("tauri.cmd" if os.name == "nt" else "tauri")
        ).is_file()
    ):
        subprocess.run([npm, "ci"], cwd=desktop, env=environment, check=True)
    binary = (
        desktop
        / "build/sidecar/dist/deepcode-app-server"
        / ("deepcode-app-server.exe" if os.name == "nt" else "deepcode-app-server")
    )
    if setup or not binary.is_file():
        for task in ("setup:sidecar", "build:sidecar"):
            subprocess.run([npm, "run", task], cwd=desktop, env=environment, check=True)
    print("Opening DeepCode Desktop. Keep this development terminal open.", flush=True)
    return subprocess.call(
        [npm, "run", "tauri", "--", "dev", *options], cwd=desktop, env=environment
    )


def _installed_app() -> Path | None:
    if sys.platform == "darwin":
        candidates = [
            Path.home() / "Applications/DeepCode.app",
            Path("/Applications/DeepCode.app"),
        ]
    elif os.name == "nt":
        candidates = [
            Path(base) / "DeepCode/deepcode-desktop.exe"
            for key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")
            if (base := os.environ.get(key))
        ]
    else:
        candidates = [
            Path(directory) / "deepcode-desktop" for directory in os.get_exec_path()
        ]
    for candidate in candidates:
        if sys.platform == "darwin" and candidate.is_dir():
            return candidate
        if candidate.is_file():
            # Do not recurse through the historical shell alias of our command.
            with candidate.open("rb") as stream:
                signature = stream.read(4)
            if signature.startswith(b"MZ") or signature == b"\x7fELF":
                return candidate
    return None


def _open_app(app: Path) -> int:
    app = app.expanduser().resolve()
    if sys.platform == "darwin" and app.suffix == ".app" and app.is_dir():
        return subprocess.call(["open", "-a", str(app)])
    if not app.is_file():
        raise ValueError(f"Desktop application was not found: {app}")
    subprocess.Popen(
        [str(app)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepcode desktop", description="Open the native DeepCode Desktop client."
    )
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--app",
        type=Path,
        help="Installed application or AppImage at a custom location.",
    )
    location.add_argument(
        "--source", type=Path, help="Use a specific trusted source checkout."
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Rebuild source dependencies and Desktop resources before opening.",
    )
    parser.add_argument(
        "options",
        nargs=argparse.REMAINDER,
        help="Optional Tauri development arguments after --.",
    )
    args = parser.parse_args(argv)
    options = args.options[1:] if args.options[:1] == ["--"] else args.options
    try:
        source = args.source.expanduser().resolve() if args.source else None
        if source is not None and not _is_checkout(source):
            raise ValueError("--source must point to a DeepCode source checkout.")
        if args.app is None:
            source = source or _source_checkout()
        if source is not None:
            return _run_source(source, setup=args.setup, options=options)
        if args.setup or options:
            raise ValueError("--setup and Tauri arguments require a source checkout.")
        app = args.app or _installed_app()
        if app is None:
            raise ValueError(
                "Desktop is not installed. Install the Desktop app, or use deepcode desktop --source /path/to/DeepCode."
            )
        return _open_app(app)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
