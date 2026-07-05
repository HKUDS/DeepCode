"""Tests for the P2 edit->diagnostics loop."""

from __future__ import annotations

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


# --- checkers ---------------------------------------------------------------


def test_pycompile_flags_syntax_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n    return 1\n")
    diags = PyCompileChecker().check(str(bad))
    assert len(diags) == 1
    assert diags[0].severity == "error"
    assert diags[0].line is not None


def test_pycompile_clean_file_no_diagnostics(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("def f():\n    return 1\n")
    assert PyCompileChecker().check(str(good)) == []


def test_registry_skips_unregistered_extension(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("this is not code {{{")
    assert run_diagnostics(str(txt)) == []


def test_registry_runs_pycompile_on_py(tmp_path):
    bad = tmp_path / "b.py"
    bad.write_text("x = (1\n")  # unbalanced paren
    diags = run_diagnostics(str(bad))
    assert any(d.source == "py_compile" and d.severity == "error" for d in diags)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_node_check_flags_js_syntax_error(tmp_path):
    bad = tmp_path / "bad.js"
    bad.write_text("function f( { return 1 }\n")
    diags = NodeCheckChecker().check(str(bad))
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
    diags = run_diagnostics(str(bad))
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

    def fake(path):
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
