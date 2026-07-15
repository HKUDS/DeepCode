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
    user_global_instructions,
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


# -- C: aligned discovery (repo-root upward + user-global) -------------------


def test_project_instructions_walks_up_to_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()  # marks the repo root
    (tmp_path / "AGENTS.md").write_text("Root: use pytest.")
    sub = tmp_path / "pkg" / "svc"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("Service: async only.")
    out = project_instructions(sub)
    # both apply; root first, nearest (workspace) last
    assert "Root: use pytest." in out and "Service: async only." in out
    assert out.index("Root: use pytest.") < out.index("Service: async only.")


def test_project_instructions_workspace_is_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("Only root.")
    out = project_instructions(tmp_path)
    assert out.count("## Project instructions") == 1 and "Only root." in out


def test_project_instructions_no_repo_reads_only_workspace(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("parent — must NOT be read (no repo)")
    (sub / "AGENTS.md").write_text("workspace only")
    out = project_instructions(sub)
    assert "workspace only" in out and "must NOT be read" not in out


def test_user_global_instructions_native_then_interop(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "AGENTS.md").write_text("global native rule")
    assert "global native rule" in user_global_instructions(home=tmp_path)

    home2 = tmp_path / "h2"
    (home2 / ".claude").mkdir(parents=True)
    (home2 / ".claude" / "CLAUDE.md").write_text("global claude rule")
    got = user_global_instructions(home=home2)
    assert "global claude rule" in got and "~/.claude/CLAUDE.md" in got

    assert user_global_instructions(home=tmp_path / "empty") == ""


def test_system_preamble_orders_global_before_project(tmp_path):
    (tmp_path / ".deepcode").mkdir()
    (tmp_path / ".deepcode" / "AGENTS.md").write_text("GLOBAL RULE")
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("PROJECT RULE")
    out = system_preamble(ws, home=tmp_path)
    assert "GLOBAL RULE" in out and "PROJECT RULE" in out
    assert out.index("GLOBAL RULE") < out.index("PROJECT RULE")
