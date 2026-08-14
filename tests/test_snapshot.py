"""Tests for the P2 shadow-git snapshotter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.snapshot import Snapshotter  # noqa: E402

pytestmark = pytest.mark.skipif(
    not Snapshotter.git_available(), reason="git not available"
)


def _snapshotter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    shadow = tmp_path / "shadow"
    return ws, Snapshotter(str(ws), shadow_dir=str(shadow))


def test_snapshot_does_not_touch_workspace_git(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "a.py").write_text("x = 1\n")
    snap.snapshot("first")
    # the workspace must NOT gain a .git of its own
    assert not (ws / ".git").exists()
    # metadata lives in the shadow dir
    assert (tmp_path / "shadow" / "HEAD").exists()


def test_diff_shows_change_after_snapshot(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "a.py").write_text("x = 1\n")
    s = snap.snapshot("base")
    (ws / "a.py").write_text("x = 2\n")
    diff = snap.diff_since(s.id)
    assert "-x = 1" in diff and "+x = 2" in diff


def test_restore_reverts_an_edit(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "a.py").write_text("original\n")
    s = snap.snapshot("base")
    (ws / "a.py").write_text("changed\n")
    assert (ws / "a.py").read_text() == "changed\n"
    snap.restore(s.id)
    assert (ws / "a.py").read_text() == "original\n"


def test_list_is_most_recent_first(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "a.py").write_text("1\n")
    snap.snapshot("one")
    (ws / "a.py").write_text("2\n")
    snap.snapshot("two")
    snaps = snap.list()
    assert [s.label for s in snaps][:2] == ["two", "one"]


def test_two_snapshots_have_distinct_ids(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "a.py").write_text("1\n")
    s1 = snap.snapshot("one")
    (ws / "a.py").write_text("2\n")
    s2 = snap.snapshot("two")
    assert s1.id != s2.id


def test_new_file_then_restore_reverts_content(tmp_path):
    ws, snap = _snapshotter(tmp_path)
    (ws / "keep.py").write_text("v1\n")
    s = snap.snapshot("base")
    (ws / "keep.py").write_text("v2\n")
    (ws / "extra.py").write_text("added later\n")
    snap.restore(s.id)
    # tracked file reverts; the checkpoint captured only keep.py
    assert (ws / "keep.py").read_text() == "v1\n"
