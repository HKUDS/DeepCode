"""Build the Python App Server as a Tauri resource bundle.

An onedir bundle is deliberate: PyInstaller onefile extracts into a new temporary
directory on every launch, which forces macOS to validate every embedded library
again. The persistent resource directory starts quickly after normal app
installation validation and works without a system Python.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = DESKTOP_ROOT.parent
BUILD_ROOT = DESKTOP_ROOT / "build" / "sidecar"
DIST_ROOT = BUILD_ROOT / "dist"
APP_SERVER_ROOT = DIST_ROOT / "deepcode-app-server"
SIDECAR_ENV_ROOT = BUILD_ROOT / ".venv"
BUNDLED_DATA = (
    (
        REPOSITORY_ROOT / "core" / "application" / "goal_prompts",
        "core/application/goal_prompts",
    ),
)
REQUIRED_IMPORTS = (
    "PyInstaller",
    "aiofiles",
    "aiohttp",
    "anthropic",
    "httpx",
    "json_repair",
    "loguru",
    "mcp",
    "openai",
    "pydantic_settings",
    "pypdf",
    "yaml",
)
EXCLUDED_MODULES = (
    # Pygments discovers its optional bitmap formatter during analysis. Desktop
    # renders code in React/Monaco and the App Server never creates images.
    "PIL",
    # OpenAI voice helpers and tiktoken type annotations are the only references.
    # DeepCode's text/tool runtime does not use NumPy.
    "numpy",
    # DeepCode uses Pydantic v2 only; the compatibility tree is unnecessary.
    "pydantic.v1",
    # No runtime path builds or installs Python distributions.
    "setuptools",
)


def target_triple() -> str:
    configured = os.environ.get("DEEPCODE_TARGET_TRIPLE") or os.environ.get(
        "CARGO_BUILD_TARGET"
    )
    if configured:
        return configured
    completed = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    raise RuntimeError("rustc did not report a host target triple")


def builder_python() -> Path:
    configured = os.environ.get("DEEPCODE_SIDECAR_PYTHON")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        reason = _invalid_builder_reason(candidate)
        if reason is not None:
            raise RuntimeError(f"invalid DEEPCODE_SIDECAR_PYTHON: {reason}")
        return candidate

    relative_python = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    candidates = (
        SIDECAR_ENV_ROOT / relative_python,
        REPOSITORY_ROOT / ".venv" / relative_python,
        Path(sys.executable),
    )
    failures: list[str] = []
    for candidate in candidates:
        reason = _invalid_builder_reason(candidate)
        if reason is None:
            return candidate
        failures.append(f"{candidate}: {reason}")
    detail = "\n".join(f"  - {failure}" for failure in failures)
    raise RuntimeError(
        "no complete Python 3.12 sidecar environment was found.\n"
        "Run `npm run setup:sidecar` from desktop or set "
        "DEEPCODE_SIDECAR_PYTHON.\n"
        f"{detail}"
    )


def _invalid_builder_reason(python: Path) -> str | None:
    if not python.is_file():
        return "executable does not exist"
    probe = (
        "import importlib, json, sys\n"
        f"names = {REQUIRED_IMPORTS!r}\n"
        "errors = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        errors[name] = f'{type(exc).__name__}: {exc}'\n"
        "print(json.dumps({'version': list(sys.version_info[:2]), 'errors': errors}))\n"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return f"probe failed: {exc}"
    if result.get("version") != [3, 12]:
        version = ".".join(str(value) for value in result.get("version", []))
        return f"Python 3.12 is required, found {version or 'unknown'}"
    errors = result.get("errors")
    if isinstance(errors, dict) and errors:
        missing = ", ".join(f"{name} ({error})" for name, error in errors.items())
        return f"required imports failed: {missing}"
    return None


def main() -> int:
    target = target_triple()
    name = "deepcode-app-server"
    shutil.rmtree(APP_SERVER_ROOT, ignore_errors=True)
    command = [
        str(builder_python()),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        name,
        "--paths",
        str(REPOSITORY_ROOT),
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT / "work" / target),
        "--specpath",
        str(BUILD_ROOT / "spec" / target),
    ]
    for module in EXCLUDED_MODULES:
        command.extend(("--exclude-module", module))
    for source, destination in BUNDLED_DATA:
        if not source.is_dir():
            raise RuntimeError(
                f"required sidecar resource directory is missing: {source}"
            )
        command.extend(("--add-data", f"{source}{os.pathsep}{destination}"))
    command.append(str(REPOSITORY_ROOT / "app_server" / "__main__.py"))
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, cwd=REPOSITORY_ROOT, check=True)
    binary = APP_SERVER_ROOT / (
        f"{name}.exe" if target.endswith("windows-msvc") else name
    )
    if not binary.is_file():
        raise RuntimeError(f"PyInstaller did not produce {binary}")
    _verify_bundle(binary)
    print(binary)
    return 0


def _verify_bundle(binary: Path) -> None:
    probe = subprocess.run(
        [str(binary), "--verify-runtime"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(probe.stdout)
    if result.get("ok") is not True:
        raise RuntimeError("packaged runtime import probe did not report success")

    smoke_root = BUILD_ROOT / "smoke"
    shutil.rmtree(smoke_root, ignore_errors=True)
    home = smoke_root / "home"
    database = smoke_root / "state.sqlite3"
    environment = dict(os.environ)
    environment["DEEPCODE_HOME"] = str(home)
    process = subprocess.Popen(
        [str(binary), "--database", str(database)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "1.0",
            "clientInfo": {"name": "sidecar-build-smoke", "version": "1"},
        },
    }
    shutdown = {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}
    try:
        stdout, stderr = process.communicate(
            f"{json.dumps(initialize)}\n{json.dumps(shutdown)}\n",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RuntimeError(
            f"packaged App Server smoke timed out: {stderr[-2_000:]}"
        ) from None
    finally:
        shutil.rmtree(smoke_root, ignore_errors=True)
    if process.returncode != 0:
        raise RuntimeError(
            f"packaged App Server smoke failed ({process.returncode}): "
            f"{stderr[-2_000:]}"
        )
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if (
        len(responses) != 2
        or responses[0].get("result", {}).get("protocolVersion") != "1.0"
    ):
        raise RuntimeError("packaged App Server smoke returned invalid RPC responses")


if __name__ == "__main__":
    raise SystemExit(main())
