"""Tests for the P2 native read / write / edit tools."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools import default_coding_tools  # noqa: E402
from core.harness.tools.files import EditTool, ReadTool, WriteTool  # noqa: E402


@pytest.mark.asyncio
async def test_write_then_read_line_numbered(tmp_path):
    w = WriteTool(str(tmp_path))
    r = ReadTool(str(tmp_path))
    out = await w.execute(file_path="a.py", content="x = 1\ny = 2\n")
    assert "Wrote" in out
    read = await r.execute(file_path="a.py")
    assert "1: x = 1" in read
    assert "2: y = 2" in read


@pytest.mark.asyncio
async def test_read_offset_limit(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    r = ReadTool(str(tmp_path))
    out = await r.execute(file_path="big.txt", offset=3, limit=2)
    assert "3: line3" in out and "4: line4" in out
    assert "line1" not in out
    assert "more lines" in out


@pytest.mark.asyncio
async def test_read_missing_file_is_error_data(tmp_path):
    r = ReadTool(str(tmp_path))
    out = await r.execute(file_path="nope.py")
    assert out.startswith("Error: file not found")


@pytest.mark.asyncio
async def test_read_directory_lists_entries(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.txt").write_text("x")
    r = ReadTool(str(tmp_path))
    out = await r.execute(file_path=".")
    assert "sub/" in out and "f.txt" in out


@pytest.mark.asyncio
async def test_edit_fuzzy_whitespace(tmp_path):
    (tmp_path / "a.py").write_text("x   =    1\n")
    e = EditTool(str(tmp_path))
    out = await e.execute(file_path="a.py", old_string="x = 1", new_string="x = 2")
    assert "Edited" in out
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


@pytest.mark.asyncio
async def test_edit_ambiguous_is_error_data(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    e = EditTool(str(tmp_path))
    out = await e.execute(file_path="a.py", old_string="x = 1", new_string="x = 2")
    assert out.startswith("Error:") and "multiple" in out.lower()
    # file unchanged on failure
    assert (tmp_path / "a.py").read_text() == "x = 1\nx = 1\n"


@pytest.mark.asyncio
async def test_edit_missing_file_is_error(tmp_path):
    e = EditTool(str(tmp_path))
    out = await e.execute(file_path="nope.py", old_string="a", new_string="b")
    assert out.startswith("Error: file not found")


@pytest.mark.asyncio
async def test_write_outside_workspace_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    w = WriteTool(str(ws))
    out = await w.execute(file_path="../escape.txt", content="leak")
    assert out.startswith("Error: refusing to write outside")
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_edit_outside_workspace_refused(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "outside.py").write_text("secret = 1\n")
    e = EditTool(str(ws))
    out = await e.execute(
        file_path="../outside.py", old_string="secret = 1", new_string="secret = 2"
    )
    assert out.startswith("Error: refusing to edit outside")
    assert (tmp_path / "outside.py").read_text() == "secret = 1\n"


def test_default_coding_tools_registry(tmp_path):
    reg = default_coding_tools(str(tmp_path))
    expected = {"read", "write", "edit", "apply_patch", "bash", "grep", "glob"}
    assert set(reg.tool_names) == expected
    # schemas self-generate from the tool classes (single source of truth)
    defs = reg.get_definitions()
    names = {d["function"]["name"] for d in defs}
    assert names == expected
