"""Tests for P4 worktree isolation + merge conflict detection (real git)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.team.worktree import _EXCLUDE_BEGIN, WorktreeManager  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


def _mgr(tmp_path) -> WorktreeManager:
    base = tmp_path / "base"
    base.mkdir()
    (base / "README.md").write_text("# project\n")
    return WorktreeManager(str(base), worktrees_root=str(tmp_path / "wt"))


def test_create_gives_isolated_checkout(tmp_path):
    m = _mgr(tmp_path)
    wt = Path(m.create("w1"))
    assert wt.is_dir()
    assert (wt / "README.md").exists()  # branched off base HEAD
    # Editing in the worktree does not touch the base tree.
    (wt / "new.py").write_text("x = 1\n")
    assert not (m.base / "new.py").exists()
    m.cleanup_all()


def test_non_overlapping_workers_merge_clean(tmp_path):
    m = _mgr(tmp_path)
    wt1 = Path(m.create("w1"))
    wt2 = Path(m.create("w2"))
    (wt1 / "a.py").write_text("A = 1\n")  # different files → no conflict
    (wt2 / "b.py").write_text("B = 2\n")

    r1 = m.merge("w1")
    r2 = m.merge("w2")
    assert r1.clean and r2.clean
    # Both changes are now in the base.
    assert (m.base / "a.py").read_text() == "A = 1\n"
    assert (m.base / "b.py").read_text() == "B = 2\n"
    m.cleanup_all()


def test_overlapping_workers_conflict_is_detected(tmp_path):
    m = _mgr(tmp_path)
    # Seed a shared file both workers edit on the same line. Written before
    # create(), so ensure_base() commits it and both worktrees branch from it.
    (m.base / "shared.py").write_text("value = 0\n")
    wt1 = Path(m.create("w1"))
    wt2 = Path(m.create("w2"))
    (wt1 / "shared.py").write_text("value = 1\n")  # same line, different value
    (wt2 / "shared.py").write_text("value = 2\n")

    r1 = m.merge("w1")
    r2 = m.merge("w2")
    assert r1.clean  # first one merges fine
    assert not r2.clean  # second overlaps → conflict, not silent clobber
    assert "shared.py" in r2.conflicts
    # The base kept w1's value; w2's conflict did not clobber it.
    assert m._git("status", "--porcelain").stdout.strip() == ""  # noqa: SLF001
    m.cleanup_all()


def test_no_changes_merge_is_clean_noop(tmp_path):
    m = _mgr(tmp_path)
    m.create("w1")  # worker does nothing
    r = m.merge("w1")
    assert r.clean and "no changes" in r.detail
    m.cleanup_all()


def test_build_artifacts_do_not_break_merge_or_pollute(tmp_path):
    """A worker's test run leaves build artifacts (e.g. __pycache__) in its
    worktree, and the base has untracked artifacts of its own. Before the fix,
    `git merge` aborted ('untracked files would be overwritten') with no
    content conflict — silently failing the task. Artifacts must be ignored, so
    the merge is clean and history stays free of them."""
    m = _mgr(tmp_path)
    m.ensure_base()  # installs the local artifact-ignore
    # Base already has an untracked artifact (as if the user ran the tests).
    (m.base / "__pycache__").mkdir()
    (m.base / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"BASE")

    wt = Path(m.create("w1"))
    (wt / "mod.py").write_text("VALUE = 1\n")  # real work
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"WORKER")  # artifact

    r = m.merge("w1")
    assert r.clean, f"merge should be clean, got: {r.detail}"
    assert (m.base / "mod.py").read_text() == "VALUE = 1\n"  # real work landed
    # The artifact was neither committed nor did it obstruct the merge.
    tracked = m._git("ls-files").stdout  # noqa: SLF001
    assert "mod.py" in tracked
    assert "__pycache__" not in tracked and ".pyc" not in tracked
    m.cleanup_all()


def test_team_exclude_is_local_idempotent_and_reverted(tmp_path):
    m = _mgr(tmp_path)
    m.ensure_base()  # installs our exclude block into .git/info/exclude
    exclude = m.base / ".git" / "info" / "exclude"
    # A user's own rule sits alongside and must survive our install/remove.
    exclude.write_text("user-secret.txt\n" + exclude.read_text())
    m._install_team_exclude()  # noqa: SLF001 - re-install is a no-op
    assert exclude.read_text().count(_EXCLUDE_BEGIN) == 1  # idempotent, no dup

    m.cleanup_all()  # leaves the repo's config as we found it
    text = exclude.read_text()
    assert _EXCLUDE_BEGIN not in text  # our block gone
    assert "user-secret.txt" in text  # the user's rule preserved
