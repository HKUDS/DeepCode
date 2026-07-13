"""Tests for P5 skills — Claude Code-compatible SKILL.md discovery + tool.

Everything here is hermetic (no ambient ~/.claude scan): discovery is pointed at
tmp dirs and, where user-level is exercised, ``home`` is overridden.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.skills import (  # noqa: E402
    SkillError,
    SkillRegistry,
    SkillTool,
    discover_skills,
    parse_skill_md,
    skills_preamble,
)


def _write_skill(
    root: Path,
    folder: str,
    *,
    description: str = "does a thing",
    body: str = "Step 1. Do it.",
    frontmatter_extra: str = "",
    skill_name: str | None = "__folder__",
) -> Path:
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    if skill_name is not None:
        lines.append(f"name: {folder if skill_name == '__folder__' else skill_name}")
    lines.append(f"description: {description}")
    if frontmatter_extra:
        lines.append(frontmatter_extra)
    (d / "SKILL.md").write_text("---\n" + "\n".join(lines) + "\n---\n\n" + body)
    return d / "SKILL.md"


# -- parsing ---------------------------------------------------------------


def test_parse_basic(tmp_path):
    p = _write_skill(tmp_path, "greet", description="greet the user", body="Say hi.")
    s = parse_skill_md(p)
    assert s.name == "greet"
    assert s.description == "greet the user"
    assert "Say hi." in s.instructions
    assert s.allowed_tools == ()
    assert s.directory == str(p.parent)


def test_parse_allowed_tools_as_list(tmp_path):
    p = _write_skill(
        tmp_path, "a", frontmatter_extra="allowed-tools:\n  - read\n  - bash"
    )
    assert parse_skill_md(p).allowed_tools == ("read", "bash")


def test_parse_allowed_tools_as_csv(tmp_path):
    p = _write_skill(tmp_path, "b", frontmatter_extra="allowed-tools: read, grep")
    assert parse_skill_md(p).allowed_tools == ("read", "grep")


def test_parse_name_falls_back_to_folder(tmp_path):
    p = _write_skill(tmp_path, "myskill", skill_name=None)  # no name field
    assert parse_skill_md(p).name == "myskill"


def test_parse_missing_frontmatter_raises(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter at all")
    with pytest.raises(SkillError):
        parse_skill_md(d / "SKILL.md")


def test_parse_missing_description_raises(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\n---\nbody")
    with pytest.raises(SkillError):
        parse_skill_md(d / "SKILL.md")


# -- discovery + precedence ------------------------------------------------


def test_discover_from_deepcode_and_claude(tmp_path):
    ws = tmp_path / "ws"
    _write_skill(ws / ".deepcode/skills", "alpha")
    _write_skill(ws / ".claude/skills", "beta")
    reg = discover_skills(ws, include_user=False)
    assert set(reg.names()) == {"alpha", "beta"}


def test_project_overrides_user(tmp_path):
    ws, home = tmp_path / "ws", tmp_path / "home"
    _write_skill(ws / ".deepcode/skills", "dup", description="PROJECT")
    _write_skill(home / ".deepcode/skills", "dup", description="USER")
    reg = discover_skills(ws, home=home)
    assert reg.get("dup").description == "PROJECT"


def test_deepcode_beats_claude_same_name(tmp_path):
    ws = tmp_path / "ws"
    _write_skill(ws / ".deepcode/skills", "dup", description="DEEPCODE")
    _write_skill(ws / ".claude/skills", "dup", description="CLAUDE")
    reg = discover_skills(ws, include_user=False)
    assert reg.get("dup").description == "DEEPCODE"


def test_include_user_false_is_hermetic(tmp_path):
    ws, home = tmp_path / "ws", tmp_path / "home"
    _write_skill(home / ".claude/skills", "userskill")
    assert len(discover_skills(ws, home=home, include_user=False)) == 0
    assert len(discover_skills(ws, home=home, include_user=True)) == 1


def test_malformed_skill_recorded_not_fatal(tmp_path):
    ws = tmp_path / "ws"
    _write_skill(ws / ".deepcode/skills", "good")
    bad = ws / ".deepcode/skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("garbage, no frontmatter")
    reg = discover_skills(ws, include_user=False)
    assert reg.names() == ["good"]  # the good one still loads
    assert reg.errors and "bad" in reg.errors[0]  # the bad one is surfaced


# -- tool + preamble -------------------------------------------------------


def test_skill_tool_loads_and_reports_unknown(tmp_path):
    ws = tmp_path / "ws"
    _write_skill(
        ws / ".deepcode/skills",
        "deploy",
        description="deploy the app",
        body="1. build\n2. ship",
        frontmatter_extra="allowed-tools: bash",
    )
    tool = SkillTool(discover_skills(ws, include_user=False))
    assert tool.read_only is True

    out = asyncio.run(tool.execute(name="deploy"))
    assert "Skill: deploy" in out
    assert "1. build" in out and "2. ship" in out
    assert "bash" in out  # intended tools surfaced

    missing = asyncio.run(tool.execute(name="nope"))
    assert "no skill named" in missing and "deploy" in missing


def test_skills_preamble(tmp_path):
    ws = tmp_path / "ws"
    _write_skill(ws / ".deepcode/skills", "x", description="do x")
    pre = skills_preamble(discover_skills(ws, include_user=False))
    assert "Available skills" in pre and "**x**: do x" in pre
    assert skills_preamble(SkillRegistry()) == ""  # no skills → nothing injected


def test_wired_into_default_coding_tools(tmp_path):
    from core.harness.tools import default_coding_tools

    # No skills in the workspace → the base tool set, no `skill` tool.
    assert "skill" not in set(default_coding_tools(str(tmp_path)).tool_names)

    # A project skill → the `skill` tool appears (hermetic: project-only scan).
    _write_skill(tmp_path / ".deepcode/skills", "y")
    assert "skill" in set(default_coding_tools(str(tmp_path)).tool_names)
