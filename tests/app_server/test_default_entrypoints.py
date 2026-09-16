from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from app_server.service_state import ServiceFiles
from core.persistence.database import default_database_path


@pytest.mark.parametrize("module", ["cli.tui", "cli.exec_cli", "app_server"])
def test_public_help_exposes_only_shared_service_usage(module):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--runtime" not in result.stdout
    assert "standalone" not in result.stdout
    assert "embedded" not in result.stdout


def test_compatibility_script_owns_its_process_without_starting_a_service(tmp_path):
    root = Path(__file__).resolve().parents[2]
    workspace = tmp_path / "project"
    workspace.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_compat.py"),
            "tui",
            "--trust",
            "-w",
            str(workspace),
        ],
        input="/exit\n",
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert not ServiceFiles(default_database_path()).running()


def test_default_automation_run_uses_service_and_keeps_its_receipt(
    tmp_path, monkeypatch, capsys, shared_cli_service
):
    import json
    from cli import automation_cli
    from core.application import DeepCodeApplication
    from core.domain import TrustState
    from core.domain.automation import AutomationScheduleKind
    from tests.test_exec_cli import _patch
    from tests.test_loop_cli import _Provider

    _patch(monkeypatch, _Provider("complete"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = DeepCodeApplication.open()
    try:
        project = app.projects.add(str(workspace), trust_state=TrustState.TRUSTED)
        created = app.automations.create(
            project_id=project.id,
            name="service-run",
            prompt="finish",
            schedule_kind=AutomationScheduleKind.MANUAL,
        )
    finally:
        app.close()
    ids = []
    for _ in range(2):
        assert (
            automation_cli.run(
                ["run", created.automation.id, "--request-id", "same-run", "--json"]
            )
            == 0
        )
        value = json.loads(capsys.readouterr().out)
        assert value["run"]["status"] == "completed"
        ids.append(value["run"]["id"])
    assert ids[0] == ids[1]
    assert ServiceFiles(default_database_path()).running()


def test_schedule_memory_maintenance_submits_a_service_turn(
    tmp_path, monkeypatch, shared_cli_service
):
    import asyncio
    from cli.schedule_cli import _autodream_task
    from core.application import DeepCodeApplication
    from core.domain import TrustState
    from core.providers.base import LLMResponse
    from tests.test_exec_cli import _patch, _ScriptedProvider

    _patch(
        monkeypatch,
        _ScriptedProvider(
            [LLMResponse(content="memory reviewed", finish_reason="stop")]
        ),
    )
    memory = tmp_path / ".deepcode/memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("An existing note.\n")
    app = DeepCodeApplication.open()
    try:
        app.projects.add(str(tmp_path), trust_state=TrustState.TRUSTED)
    finally:
        app.close()
    outcome = asyncio.run(_autodream_task(str(tmp_path), None, None, None)(0))
    assert outcome.goal_reached
    assert ServiceFiles(default_database_path()).running()


def test_cancelled_mcp_waiter_detaches_without_cancelling_the_task(
    tmp_path, monkeypatch, shared_cli_service
):
    import asyncio
    from cli.mcp_server import _handle_deepcode
    from core.application import DeepCodeApplication
    from core.domain import TrustState
    from tests.test_exec_cli import _patch
    from tests.test_loop_cli import _BlockingProvider

    provider = _BlockingProvider()
    _patch(monkeypatch, provider)
    app = DeepCodeApplication.open()
    try:
        app.projects.add(str(tmp_path), trust_state=TrustState.TRUSTED)
    finally:
        app.close()

    async def scenario():
        call = asyncio.create_task(
            _handle_deepcode(
                {"prompt": "finish after detach", "workspace": str(tmp_path)}
            )
        )
        try:
            assert await asyncio.to_thread(provider.entered.wait, 5)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert ServiceFiles(default_database_path()).running()
        finally:
            provider.release.set()
            await asyncio.gather(call, return_exceptions=True)

    asyncio.run(scenario())

    from app_server.blocking_client import BlockingServiceClient
    from core.sessions import SessionStore
    import time

    rpc = BlockingServiceClient(ServiceFiles(default_database_path()))
    try:
        session_id = SessionStore().list_sessions()[0].session_id
        deadline = time.monotonic() + 5
        while True:
            turn = rpc.call("turn/list", {"threadId": session_id, "limit": 1})["turns"][
                0
            ]
            if turn["status"] in {"completed", "failed", "interrupted"}:
                assert turn["status"] == "completed"
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
    finally:
        rpc.close()
