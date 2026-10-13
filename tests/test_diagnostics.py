"""Tests for the P2 edit->diagnostics loop."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.diagnostics import (  # noqa: E402
    Diagnostic,
    NodeCheckChecker,
    PyCompileChecker,
    format_diagnostics,
    run_diagnostics,
)
from core.harness.tools.files import EditTool, WriteTool  # noqa: E402
from core.harness.tools.patch import ApplyPatchTool  # noqa: E402


# --- checkers ---------------------------------------------------------------


def test_pycompile_flags_syntax_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n    return 1\n")
    diags = asyncio.run(PyCompileChecker().check(str(bad)))
    assert len(diags) == 1
    assert diags[0].severity == "error"
    assert diags[0].line is not None


def test_pycompile_clean_file_no_diagnostics(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("def f():\n    return 1\n")
    assert asyncio.run(PyCompileChecker().check(str(good))) == []


@pytest.mark.parametrize("source", ["def f():\n    return 1\n", "def f(:\n"])
def test_pycompile_writes_no_bytecode_into_the_project(tmp_path, source):
    path = tmp_path / "m.py"
    path.write_text(source)
    asyncio.run(PyCompileChecker().check(str(path)))
    assert list(tmp_path.iterdir()) == [path]


def test_registry_skips_unregistered_extension(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("this is not code {{{")
    assert asyncio.run(run_diagnostics(str(txt))) == []


def test_registry_runs_pycompile_on_py(tmp_path):
    bad = tmp_path / "b.py"
    bad.write_text("x = (1\n")  # unbalanced paren
    diags = asyncio.run(run_diagnostics(str(bad)))
    assert any(d.source == "py_compile" and d.severity == "error" for d in diags)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_node_check_flags_js_syntax_error(tmp_path):
    bad = tmp_path / "bad.js"
    bad.write_text("function f( { return 1 }\n")
    diags = asyncio.run(NodeCheckChecker().check(str(bad)))
    assert any(d.severity == "error" for d in diags)


def test_format_diagnostics_error_header():
    out = format_diagnostics(
        [Diagnostic(line=3, column=1, severity="error", message="bad", source="x")]
    )
    assert "please fix" in out
    assert "line 3" in out


def test_format_empty_is_blank():
    assert format_diagnostics([]) == ""


def test_unavailable_checker_skipped(tmp_path, monkeypatch):
    # Force ruff unavailable; py_compile still runs.
    from core.harness.tools import diagnostics as diag

    monkeypatch.setattr(diag.shutil, "which", lambda name: None)
    bad = tmp_path / "b.py"
    bad.write_text("def f(:\n")
    diags = asyncio.run(run_diagnostics(str(bad)))
    # py_compile (always available) still fires; node/ruff skipped
    assert all(d.source != "ruff" for d in diags)
    assert any(d.source == "py_compile" for d in diags)


# --- tool wiring ------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_appends_diagnostics_for_broken_python(tmp_path):
    w = WriteTool(str(tmp_path))
    out = await w.execute(file_path="broken.py", content="def f(:\n    return 1\n")
    assert "Wrote" in out
    assert "Diagnostics detected" in out
    # file IS written (so the model can then fix it)
    assert (tmp_path / "broken.py").exists()


@pytest.mark.asyncio
async def test_write_clean_python_no_diagnostics_block(tmp_path):
    w = WriteTool(str(tmp_path))
    out = await w.execute(file_path="ok.py", content="def f():\n    return 1\n")
    assert "Diagnostics" not in out


@pytest.mark.asyncio
async def test_edit_that_breaks_syntax_is_flagged(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    e = EditTool(str(tmp_path))
    # replace the body with something syntactically broken
    out = await e.execute(
        file_path="a.py", old_string="    return 1", new_string="    return (1"
    )
    assert "Edited" in out
    assert "Diagnostics detected" in out


@pytest.mark.asyncio
async def test_diagnostics_injection_is_used(tmp_path):
    calls = []

    async def fake(path):
        calls.append(path)
        return [
            Diagnostic(
                line=1, column=1, severity="error", message="injected", source="fake"
            )
        ]

    w = WriteTool(str(tmp_path), diagnostics=fake)
    out = await w.execute(file_path="x.py", content="x = 1\n")
    assert calls  # the injected checker was called
    assert "injected" in out


@pytest.mark.skipif(sys.platform == "win32", reason="runs a shell script")
@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["write", "edit", "apply_patch"])
async def test_diagnostics_do_not_hold_up_the_event_loop(
    tmp_path, monkeypatch, tool_name
):
    # ruff and node --check may take seconds; other sessions must keep going.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "ruff"
    stand_in.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep(1)\nprint('[]')\n"
    )
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    (tmp_path / "a.py").write_text("x = 1\n")
    if tool_name == "write":
        run = WriteTool(str(tmp_path)).execute(file_path="a.py", content="x = 2\n")
    elif tool_name == "edit":
        run = EditTool(str(tmp_path)).execute(
            file_path="a.py", old_string="x = 1", new_string="x = 2"
        )
    else:
        run = ApplyPatchTool(str(tmp_path)).execute(
            patch="*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n"
            "*** End Patch\n"
        )
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        out = await run
    finally:
        ticker.cancel()

    assert not out.startswith("Error")
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    assert ticks >= 5


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group assertion")
@pytest.mark.asyncio
async def test_cancelling_a_write_ends_its_checker(tmp_path, monkeypatch):
    # An interrupted turn must not leave ruff (allowed 20s) running on.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "ruff.pid"
    stand_in = bin_dir / "ruff"
    stand_in.write_text(
        f"#!{sys.executable}\nimport os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n"
    )
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(
        WriteTool(str(tmp_path)).execute(file_path="a.py", content="x = 1\n")
    )
    for _ in range(500):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.01)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(200):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        os.kill(pid, 9)  # don't leave it running past the test
        pytest.fail("ruff survived the cancelled write")
