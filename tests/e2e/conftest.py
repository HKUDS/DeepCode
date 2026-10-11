"""A private DeepCode installation for terminal tests.

Each test module gets its own DeepCode home, a scripted model, and a real
background service process; every TUI a test opens is a real process on a
pseudo-terminal talking to that service. Nothing here reads or writes the
user's own ``~/.deepcode``: the child processes get a temporary home and a
temporary ``HOME``, and no provider credentials.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

if sys.platform == "win32":  # pragma: no cover - no pseudo-terminals
    collect_ignore_glob = ["test_*.py"]
else:
    from app_server.service_client import ServiceClient
    from app_server.service_state import ServiceFiles

    from tests.e2e.fake_model import FakeModel
    from tests.e2e.terminal import Terminal

REPO = Path(__file__).resolve().parents[2]
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN")
_PROXY_VARIABLES = {"http_proxy", "https_proxy", "all_proxy"}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _child_environment(home: Path, user_home: Path) -> dict:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.endswith(_CREDENTIAL_SUFFIXES)
        and name.lower() not in _PROXY_VARIABLES
        and not name.startswith("DEEPCODE_")
        and not name.startswith("PYTEST_")
    }
    env.update(
        DEEPCODE_HOME=str(home),
        DEEPCODE_SESSIONS_DIR=str(home / "sessions"),
        HOME=str(user_home),
        NO_PROXY="127.0.0.1,localhost",
        PYTHONPATH=os.pathsep.join(
            [str(REPO), *filter(None, [os.environ.get("PYTHONPATH")])]
        ),
        PYTHONUNBUFFERED="1",
    )
    return env


def _write_config(home: Path, model: FakeModel) -> None:
    config = {
        "agents": {"defaults": {"connection": "fake", "model": model.model_id}},
        "providers": {
            "profiles": {
                "fake": {
                    "label": "Scripted test model",
                    "template": "vllm",
                    "apiBase": model.api_base,
                    "protocol": "openai_chat",
                    "auth": "none",
                    "modelCatalog": "manual",
                    "manualModels": [
                        {
                            "id": model.model_id,
                            "contextWindow": 65536,
                            "maxOutputTokens": 4096,
                            "reasoningEfforts": ["off", "low", "medium", "high"],
                            "toolCalling": True,
                        }
                    ],
                }
            }
        },
    }
    home.mkdir(parents=True, exist_ok=True)
    (home / "deepcode_config.json").write_text(json.dumps(config, indent=2))


class Installation:
    """The service, the scripted model, and a way to open TUIs on them."""

    def __init__(self, root: Path) -> None:
        self.home = root / "deepcode-home"
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True)
        user_home = root / "user-home"
        user_home.mkdir()
        self.model = FakeModel().start()
        _write_config(self.home, self.model)
        self.env = _child_environment(self.home, user_home)
        self.files = ServiceFiles(self.home / "state" / "deepcode.sqlite3")
        self.port = _free_port()
        self.service = None
        self.terminals: list[Terminal] = []

    # -- service ------------------------------------------------------------

    def start_service(self) -> None:
        self.files.database.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.home / "service-output.log", "ab")  # noqa: SIM115
        self.service = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app_server.service",
                "--database",
                str(self.files.database),
                "--port",
                str(self.port),
                "--log-file",
            ],
            cwd=str(self.home),
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        log.close()
        deadline = time.monotonic() + 60
        while True:
            try:
                self.status()
                return
            except Exception as exc:  # noqa: BLE001 - keep probing until ready
                if self.service.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"the test service did not start: {exc}\n{self.service_output()}"
                    ) from exc
                time.sleep(0.2)

    def service_output(self) -> str:
        path = self.home / "service-output.log"
        return path.read_text(errors="replace")[-4000:] if path.exists() else ""

    def status(self) -> dict:
        return ServiceClient(self.files).call("status", timeout=5)

    def wait_idle(self, *, timeout: float = 30.0) -> None:
        """Wait until no Turn is running or queued, then let the TUI catch up."""

        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if not status["activeTurns"] and not status["queuedTurns"]:
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"Turns still running after {timeout}s: {status}")
            time.sleep(0.1)
        time.sleep(0.6)  # the TUI polls the event log every 50 ms

    def wait_active(self, *, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while not self.status()["activeTurns"]:
            if time.monotonic() > deadline:
                raise AssertionError(f"no Turn started within {timeout}s")
            time.sleep(0.05)

    def stop_service(self) -> None:
        if self.service is None or self.service.poll() is not None:
            return
        try:
            ServiceClient(self.files).call(
                "stop", {"timeout": 5, "cancelRunning": True}, timeout=15
            )
            self.service.wait(timeout=20)
        except Exception:  # noqa: BLE001 - fall back to signals
            self.service.terminate()
            try:
                self.service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.service.kill()
                self.service.wait(timeout=10)

    # -- TUI ----------------------------------------------------------------

    def open_tui(self, *arguments: str) -> Terminal:
        terminal = Terminal(
            [
                sys.executable,
                "-m",
                "deepcode",
                "--workspace",
                str(self.workspace),
                "--trust",
                *arguments,
            ],
            env=self.env,
            cwd=str(self.workspace),
        )
        self.terminals.append(terminal)
        terminal.wait_for(r"/help for commands", timeout=60)
        return terminal

    def close(self) -> None:
        for terminal in self.terminals:
            terminal.close()
        self.stop_service()
        self.model.close()


@pytest.fixture(scope="module")
def installation(tmp_path_factory):
    deepcode = Installation(tmp_path_factory.mktemp("e2e"))
    try:
        deepcode.start_service()
        yield deepcode
    finally:
        deepcode.close()


@pytest.fixture
def deepcode(installation):
    """The module's installation, with a fresh script for this test."""

    installation.model.reset()
    yield installation
    for terminal in installation.terminals:
        terminal.close()
    installation.terminals.clear()
