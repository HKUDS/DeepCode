"""P1.b1 — execute_bash / execute_python sandbox wiring.

Backend-agnostic gating tests + real macOS seatbelt enforcement driven
through the actual MCP server functions (skipped off Darwin).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.sandbox import build_exec_command  # noqa: E402

_seatbelt = platform.system() == "Darwin" and os.path.exists("/usr/bin/sandbox-exec")


# ---- build_exec_command gating (backend-agnostic) --------------------------


def test_requires_exactly_one_of_command_or_argv(tmp_path):
    with pytest.raises(ValueError):
        build_exec_command(workspace=tmp_path)
    with pytest.raises(ValueError):
        build_exec_command(command="x", argv=["y"], workspace=tmp_path)


def test_disabled_via_env_returns_bare(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPCODE_SANDBOX", "0")
    w = build_exec_command(command="echo hi", workspace=tmp_path)
    assert w.backend == "disabled"
    assert w.argv == ["/bin/bash", "-c", "echo hi"]
    wa = build_exec_command(argv=["python", "x.py"], workspace=tmp_path)
    assert wa.backend == "disabled"
    assert wa.argv == ["python", "x.py"]


def test_enabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPCODE_SANDBOX", raising=False)
    w = build_exec_command(command="echo hi", workspace=tmp_path)
    # backend is seatbelt/bwrap on supported platforms, else "none" — never
    # "disabled" (which only happens when explicitly turned off).
    assert w.backend in ("seatbelt", "bwrap", "none")


# ---- real seatbelt enforcement through the MCP server ----------------------


@pytest.fixture
def impl_server(monkeypatch):
    import tools.code_implementation_server as srv

    return srv


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_execute_bash_write_inside_workspace_ok(impl_server, tmp_path):
    impl_server.initialize_workspace(str(tmp_path))
    out = await impl_server.execute_bash("echo hi > inside.txt", timeout=30)
    data = json.loads(out)
    assert data["status"] == "success", data
    assert data["sandbox"] == "seatbelt"
    assert (tmp_path / "inside.txt").read_text().strip() == "hi"


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_execute_bash_write_outside_workspace_blocked(impl_server, tmp_path):
    impl_server.initialize_workspace(str(tmp_path))
    leak = Path.home() / ".deepcode_exec_leak_test.txt"
    if leak.exists():
        leak.unlink()
    try:
        out = await impl_server.execute_bash(f"echo leak > {leak}", timeout=30)
        data = json.loads(out)
        leaked = leak.exists()
    finally:
        if leak.exists():
            leak.unlink()
    assert data["status"] == "error", data
    assert not leaked


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_execute_bash_credential_exfil_write_blocked(impl_server, tmp_path):
    """A command that tries to copy a secret OUT to a file it controls is
    blocked at the write step (the sandbox's real protection for shell)."""
    impl_server.initialize_workspace(str(tmp_path))
    stolen = Path.home() / ".deepcode_stolen_secret.txt"
    if stolen.exists():
        stolen.unlink()
    try:
        out = await impl_server.execute_bash(
            f"cat ~/.aws/credentials > {stolen} 2>/dev/null || echo done", timeout=30
        )
        json.loads(out)  # valid JSON result
        exfiltrated = stolen.exists() and stolen.stat().st_size > 0
    finally:
        if stolen.exists():
            stolen.unlink()
    assert not exfiltrated


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_execute_python_write_outside_blocked(impl_server, tmp_path):
    impl_server.initialize_workspace(str(tmp_path))
    leak = Path.home() / ".deepcode_py_leak_test.txt"
    if leak.exists():
        leak.unlink()
    code = f"open({str(leak)!r}, 'w').write('leak')"
    try:
        out = await impl_server.execute_python(code, timeout=30)
        data = json.loads(out)
        leaked = leak.exists()
    finally:
        if leak.exists():
            leak.unlink()
    assert data["status"] == "error", data
    assert data["sandbox"] == "seatbelt"
    assert not leaked


@pytest.mark.skipif(not _seatbelt, reason="seatbelt sandbox not available")
@pytest.mark.asyncio
async def test_execute_python_write_inside_ok(impl_server, tmp_path):
    impl_server.initialize_workspace(str(tmp_path))
    code = "open('out.txt', 'w').write('ok')"
    out = await impl_server.execute_python(code, timeout=30)
    data = json.loads(out)
    assert data["status"] == "success", data
    assert (tmp_path / "out.txt").read_text() == "ok"
