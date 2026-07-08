"""Tests for agent memory — project instructions + persistent notes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.memory import (  # noqa: E402
    MemoryTool,
    memory_dir,
    project_instructions,
    system_preamble,
)


def _run(tool: MemoryTool, **kw):
    return asyncio.run(tool.execute(**kw))


# -- project instructions ----------------------------------------------------


def test_project_instructions_prefers_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Always use tabs.")
    (tmp_path / "CLAUDE.md").write_text("Always use spaces.")
    out = project_instructions(tmp_path)
    assert "Always use tabs." in out
    assert "AGENTS.md" in out
    assert "spaces" not in out  # AGENTS.md wins over CLAUDE.md


def test_project_instructions_absent(tmp_path):
    assert project_instructions(tmp_path) == ""


def test_system_preamble_always_mentions_tool(tmp_path):
    # Even with no files, the preamble tells the model the memory tool exists.
    pre = system_preamble(tmp_path)
    assert "memory" in pre.lower()


def test_system_preamble_includes_memory_index(tmp_path):
    mem = memory_dir(tmp_path)
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- prefers dark mode")
    pre = system_preamble(tmp_path)
    assert "prefers dark mode" in pre


# -- memory tool -------------------------------------------------------------


def test_write_read_append_list_delete(tmp_path):
    tool = MemoryTool(str(tmp_path))

    assert _run(tool, action="list") == "(no memory yet)"

    r = _run(tool, action="write", name="MEMORY.md", content="fact one")
    assert "Saved memory" in r
    assert _run(tool, action="read", name="MEMORY.md") == "fact one"

    _run(tool, action="append", name="MEMORY.md", content="fact two")
    assert _run(tool, action="read", name="MEMORY.md") == "fact one\nfact two"

    assert _run(tool, action="list") == "MEMORY.md"

    assert "Deleted" in _run(tool, action="delete", name="MEMORY.md")
    assert _run(tool, action="list") == "(no memory yet)"


def test_memory_is_persistent_across_tool_instances(tmp_path):
    _run(MemoryTool(str(tmp_path)), action="write", name="n.md", content="remembered")
    # A fresh tool (new session) reads what the previous one wrote.
    assert _run(MemoryTool(str(tmp_path)), action="read", name="n.md") == "remembered"


def test_name_traversal_refused(tmp_path):
    tool = MemoryTool(str(tmp_path))
    out = _run(tool, action="write", name="../escape.md", content="x")
    assert out.startswith("Error") and "invalid memory name" in out
    assert not (tmp_path.parent / "escape.md").exists()


def test_write_requires_content(tmp_path):
    out = _run(MemoryTool(str(tmp_path)), action="write", name="x.md", content="")
    assert out.startswith("Error")


def test_memory_lives_under_dot_deepcode(tmp_path):
    _run(MemoryTool(str(tmp_path)), action="write", name="a.md", content="hi")
    assert (tmp_path / ".deepcode" / "memory" / "a.md").read_text() == "hi"
