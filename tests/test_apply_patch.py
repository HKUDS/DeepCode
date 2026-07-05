"""Tests for the apply_patch multi-file semantic-anchor tool."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.tools.patch import ApplyPatchTool, PatchError, parse_patch  # noqa: E402


def _apply(tool: ApplyPatchTool, patch: str) -> str:
    return asyncio.run(tool.execute(patch=patch))


def _tool(tmp_path) -> ApplyPatchTool:
    # Disable diagnostics so tests assert on the patch outcome, not linters.
    return ApplyPatchTool(str(tmp_path), diagnostics=lambda _p: [])


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


def test_diagnostics_surface(tmp_path):
    # A real Python syntax error should be reported by the injected checker.
    def fake_diag(_path):
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
