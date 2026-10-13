"""Tests for the P2 native read / write / edit tools."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
)
from core.harness.tools import default_coding_tools
from core.harness.tools import files
from core.harness.tools.files import EditTool, ReadTool, WriteTool


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
async def test_read_reaches_past_the_first_256kb(tmp_path):
    # 4000 lines of ~100 bytes: line 3000 sits well past the first 256 KB.
    lines = [f"line{i:05d} " + "x" * 90 for i in range(1, 4001)]
    (tmp_path / "big.txt").write_text("\n".join(lines) + "\n")
    r = ReadTool(str(tmp_path))

    out = await r.execute(file_path="big.txt", offset=3000, limit=2)
    assert out.startswith("3000: line03000 ")
    assert "3001: line03001 " in out
    assert "use offset=3002 to continue" in out

    # The default window is 2000 lines; it must not stop short silently.
    first = await r.execute(file_path="big.txt")
    assert "2000: line02000 " in first
    assert "use offset=2001 to continue" in first


@pytest.mark.asyncio
async def test_read_output_is_capped_with_a_way_to_continue(tmp_path):
    line = "y" * 1500
    (tmp_path / "wide.txt").write_text("\n".join([line] * 400))
    r = ReadTool(str(tmp_path))

    out = await r.execute(file_path="wide.txt")
    shown = out.count(": " + line)
    assert 0 < shown < 400
    assert len(out.encode()) <= 256 * 1024 + 200
    assert f"use offset={shown + 1} to continue" in out

    # A line longer than the read budget is still one (truncated) line.
    (tmp_path / "minified.js").write_text("a" * 600_000 + "\nb\n")
    out = await r.execute(file_path="minified.js")
    assert out == "1: " + "a" * 2000 + "… [truncated]\n2: b"


@pytest.mark.asyncio
async def test_read_offset_past_the_end_is_not_an_empty_file(tmp_path):
    (tmp_path / "small.txt").write_text("a\nb\nc\n")
    r = ReadTool(str(tmp_path))

    out = await r.execute(file_path="small.txt", offset=10)
    assert out.startswith("Error:")
    assert "3 lines" in out
    assert await r.execute(file_path="small.txt") == "1: a\n2: b\n3: c"

    (tmp_path / "empty.txt").write_text("")
    assert await r.execute(file_path="empty.txt") == "(empty file)"


async def _ticks_during(work) -> int:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        await work
    finally:
        ticker.cancel()
    return ticks


@pytest.mark.asyncio
async def test_read_does_not_hold_up_the_event_loop(tmp_path, monkeypatch):
    # Streaming to a far offset in a huge file takes a while; other
    # sessions on the service's loop must keep going meanwhile.
    real = files._read_window

    def slow(*args):
        time.sleep(1)
        return real(*args)

    monkeypatch.setattr(files, "_read_window", slow)
    (tmp_path / "a.txt").write_text("x\n")

    assert await _ticks_during(ReadTool(str(tmp_path)).execute(file_path="a.txt")) >= 5


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


@pytest.mark.asyncio
async def test_full_access_profile_allows_write_and_edit_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    registry = default_coding_tools(
        workspace,
        execution_security_profile=ExecutionSecurityProfile.for_preset(
            ExecutionAccessPreset.FULL_ACCESS
        ),
    )

    wrote = await registry.execute(
        "write",
        {"file_path": str(outside), "content": "before\n"},
    )
    edited = await registry.execute(
        "edit",
        {
            "file_path": str(outside),
            "old_string": "before",
            "new_string": "after",
        },
    )

    assert "Wrote" in wrote
    assert "Edited" in edited
    assert outside.read_text() == "after\n"


def test_default_coding_tools_registry(tmp_path):
    reg = default_coding_tools(str(tmp_path))
    expected = {
        "read",
        "write",
        "edit",
        "apply_patch",
        "bash",
        "grep",
        "glob",
        "memory",
        "skill",
        "update_plan",
        "web_fetch",
    }
    assert set(reg.tool_names) == expected
    # schemas self-generate from the tool classes (single source of truth)
    defs = reg.get_definitions()
    names = {d["function"]["name"] for d in defs}
    assert names == expected
