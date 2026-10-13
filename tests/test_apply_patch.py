"""Tests for the apply_patch multi-file semantic-anchor tool."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.domain.execution_security import (
    ExecutionAccessPreset,
    ExecutionSecurityProfile,
)
from core.harness.tools import default_coding_tools
from core.harness.tools.patch import (
    ApplyPatchTool,
    PatchError,
    parse_patch,
)


def _apply(tool: ApplyPatchTool, patch: str) -> str:
    return asyncio.run(tool.execute(patch=patch))


async def _no_diagnostics(_path: str) -> list:
    return []


def _tool(tmp_path) -> ApplyPatchTool:
    # Disable diagnostics so tests assert on the patch outcome, not linters.
    return ApplyPatchTool(str(tmp_path), diagnostics=_no_diagnostics)


# --- parsing (pure) --------------------------------------------------------


def test_parse_add_update_delete():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+print('a')\n"
        "*** Update File: b.py\n"
        "@@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** Delete File: c.py\n"
        "*** End Patch\n"
    )
    ops = parse_patch(patch)
    assert [o.kind for o in ops] == ["add", "update", "delete"]
    assert ops[0].add_content == "print('a')"
    assert ops[1].hunks[0].before == "def f():\n    return 1"
    assert ops[1].hunks[0].after == "def f():\n    return 2"


def test_parse_requires_envelope():
    try:
        parse_patch("*** Add File: x\n+1\n")
    except PatchError as exc:
        assert "Begin Patch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected PatchError")


def test_parse_rejects_contextonly_update():
    patch = "*** Begin Patch\n*** Update File: b.py\n@@\n unchanged\n*** End Patch\n"
    try:
        parse_patch(patch)
    except PatchError as exc:
        assert "no change hunks" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected PatchError")


# --- application (I/O, atomic) ---------------------------------------------


def test_add_update_delete_together(tmp_path):
    (tmp_path / "b.py").write_text("def f():\n    return 1\n")
    (tmp_path / "c.py").write_text("dead\n")
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+print('new')\n"
        "*** Update File: b.py\n"
        "@@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** Delete File: c.py\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Applied patch")
    assert (tmp_path / "a.py").read_text() == "print('new')"
    assert (tmp_path / "b.py").read_text() == "def f():\n    return 2\n"
    assert not (tmp_path / "c.py").exists()


def test_hunk_tolerates_indentation_drift(tmp_path):
    # File has tab indent; patch context uses spaces — fuzzy replace bridges it.
    (tmp_path / "g.py").write_text("def g():\n\treturn old()\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: g.py\n"
        "@@\n"
        " def g():\n"
        "-    return old()\n"
        "+    return new()\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Applied patch")
    assert "new()" in (tmp_path / "g.py").read_text()


def test_move_file(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: old.py\n"
        "*** Move to: sub/new.py\n"
        "@@\n"
        "-x = 1\n"
        "+x = 2\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Applied patch")
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "sub" / "new.py").read_text() == "x = 2\n"


def test_atomic_rollback_on_failure(tmp_path):
    # a.py would add fine, but the update targets a missing file → whole
    # patch aborts and a.py must NOT have been written.
    patch = (
        "*** Begin Patch\n"
        "*** Add File: a.py\n"
        "+print('a')\n"
        "*** Update File: missing.py\n"
        "@@\n"
        "-1\n"
        "+2\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Error")
    assert "missing.py" in out
    assert not (tmp_path / "a.py").exists()  # rolled back (never written)


def test_sections_for_one_file_apply_in_order(tmp_path):
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: m.py\n"
        "@@\n"
        "-a = 1\n"
        "+a = 10\n"
        "*** Update File: m.py\n"
        "@@\n"
        "-c = 3\n"
        "+c = 30\n"
        "*** Add File: n.py\n"
        "+x = 1\n"
        "*** Update File: n.py\n"
        "@@\n"
        "-x = 1\n"
        "+x = 2\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Applied patch")
    assert (tmp_path / "m.py").read_text() == "a = 10\nb = 2\nc = 30\n"
    assert (tmp_path / "n.py").read_text() == "x = 2"


def test_a_later_section_sees_an_earlier_delete(tmp_path):
    (tmp_path / "gone.py").write_text("x = 1\n")
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: gone.py\n"
        "*** Update File: gone.py\n"
        "@@\n"
        "-x = 1\n"
        "+x = 2\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Error") and "gone.py" in out
    assert (tmp_path / "gone.py").read_text() == "x = 1\n"


def test_a_failed_write_leaves_every_file_as_it_was(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "doomed.py").write_text("keep\n")
    # A file where the patch needs a directory: planning can't see it coming,
    # writing fails after a.py is done.
    (tmp_path / "blocker").write_text("not a directory\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@\n"
        "-x = 1\n"
        "+x = 2\n"
        "*** Delete File: doomed.py\n"
        "*** Add File: blocker/new.py\n"
        "+y = 1\n"
        "*** End Patch\n"
    )
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Error")
    assert "nothing was changed" in out
    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    assert (tmp_path / "doomed.py").read_text() == "keep\n"
    assert (tmp_path / "blocker").read_text() == "not a directory\n"


def test_add_existing_file_is_error(tmp_path):
    (tmp_path / "a.py").write_text("keep\n")
    patch = "*** Begin Patch\n*** Add File: a.py\n+overwrite\n*** End Patch\n"
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Error") and "already exists" in out
    assert (tmp_path / "a.py").read_text() == "keep\n"


def test_refuses_escape_workspace(tmp_path):
    patch = "*** Begin Patch\n*** Delete File: ../evil.py\n*** End Patch\n"
    out = _apply(_tool(tmp_path), patch)
    assert out.startswith("Error") and "outside the workspace" in out


def test_full_access_profile_allows_patch_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("before\n")
    registry = default_coding_tools(
        workspace,
        execution_security_profile=ExecutionSecurityProfile.for_preset(
            ExecutionAccessPreset.FULL_ACCESS
        ),
    )
    tool = registry.get("apply_patch")
    assert isinstance(tool, ApplyPatchTool)
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {outside}\n"
        "@@\n"
        "-before\n"
        "+after\n"
        "*** End Patch\n"
    )

    out = _apply(tool, patch)

    assert out.startswith("Applied patch")
    assert outside.read_text() == "after\n"


def test_diagnostics_surface(tmp_path):
    # A real Python syntax error should be reported by the injected checker.
    async def fake_diag(_path):
        from core.harness.tools.diagnostics import Diagnostic

        return [
            Diagnostic(
                line=1, column=None, severity="error", message="bad", source="py"
            )
        ]

    tool = ApplyPatchTool(str(tmp_path), diagnostics=fake_diag)
    patch = "*** Begin Patch\n*** Add File: a.py\n+def (\n*** End Patch\n"
    out = _apply(tool, patch)
    assert "a.py:" in out and "bad" in out
