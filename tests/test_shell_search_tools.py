"""Tests for the P2 native bash / grep / glob tools."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.search import GlobTool, GrepTool  # noqa: E402
from core.harness.tools.shell import BashTool, _preflight  # noqa: E402


# --- bash -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_runs_and_captures_output(tmp_path):
    b = BashTool(str(tmp_path))
    out = await b.execute(command="echo hello-deepcode")
    assert "hello-deepcode" in out


@pytest.mark.asyncio
async def test_bash_nonzero_exit_reported(tmp_path):
    b = BashTool(str(tmp_path))
    out = await b.execute(command="exit 3")
    assert "exit 3" in out


@pytest.mark.asyncio
async def test_bash_empty_command_is_error(tmp_path):
    b = BashTool(str(tmp_path))
    assert (await b.execute(command="   ")).startswith("Error: empty")


def test_preflight_blocks_interactive_scaffold():
    assert _preflight("npx create-next-app my-app") is not None
    assert _preflight("npm init") is not None


def test_preflight_allows_non_interactive():
    assert _preflight("npm init -y") is None
    assert _preflight("npx create-next-app my-app --yes") is None
    assert _preflight("pytest -q") is None


@pytest.mark.asyncio
async def test_bash_preflight_refuses(tmp_path):
    b = BashTool(str(tmp_path))
    out = await b.execute(command="npm init")
    assert out.startswith("Error:") and "hang" in out


@pytest.mark.asyncio
async def test_bash_large_output_spilled(tmp_path):
    b = BashTool(str(tmp_path))
    # produce > 30k chars
    out = await b.execute(command="python -c \"print('x' * 40000)\"")
    assert "output truncated" in out and "saved to:" in out


# --- grep -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("x = 2\n")
    g = GrepTool(str(tmp_path))
    out = await g.execute(pattern="def foo")
    assert "a.py" in out and "def foo" in out


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    g = GrepTool(str(tmp_path))
    out = await g.execute(pattern="zzz_not_here")
    assert out == "No matches."


@pytest.mark.asyncio
async def test_grep_include_filter(tmp_path):
    (tmp_path / "a.py").write_text("TARGET\n")
    (tmp_path / "b.txt").write_text("TARGET\n")
    g = GrepTool(str(tmp_path))
    out = await g.execute(pattern="TARGET", include="*.py")
    assert "a.py" in out and "b.txt" not in out


@pytest.mark.asyncio
async def test_grep_invalid_regex_is_error(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    g = GrepTool(str(tmp_path))
    out = await g.execute(pattern="(unclosed")
    assert out.startswith("Error: invalid regular expression")


# --- glob -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_glob_matches_recursively(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x")
    (tmp_path / "top.py").write_text("y")
    (tmp_path / "note.txt").write_text("z")
    g = GlobTool(str(tmp_path))
    out = await g.execute(pattern="**/*.py")
    assert "src/m.py" in out and "top.py" in out and "note.txt" not in out


@pytest.mark.asyncio
async def test_glob_skips_ignored_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("x")
    (tmp_path / "keep.py").write_text("y")
    g = GlobTool(str(tmp_path))
    out = await g.execute(pattern="**/*.py")
    assert "keep.py" in out and "node_modules" not in out


# (the default_coding_tools full-set assertion lives in
# test_native_file_tools.py::test_default_coding_tools_registry)
