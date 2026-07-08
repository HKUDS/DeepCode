"""Tests for the restricted directory-browse API (workspace picker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BACKEND = ROOT / "new_ui" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from api.routes import agent_fs  # noqa: E402


def test_lists_only_directories_under_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    (home / ".hidden").mkdir()
    (home / "note.txt").write_text("x")
    monkeypatch.setattr(agent_fs, "_HOME", home.resolve())

    import asyncio

    res = asyncio.run(agent_fs.list_dirs(path=""))
    names = [d["name"] for d in res["dirs"]]
    assert "proj" in names
    assert ".hidden" not in names  # dotfolders hidden
    assert "note.txt" not in names  # files never listed
    assert res["home"] == str(home.resolve())
    assert res["parent"] is None  # at home root


def test_fence_rejects_escape(monkeypatch, tmp_path):
    from fastapi import HTTPException

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(agent_fs, "_HOME", home.resolve())

    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(agent_fs.list_dirs(path=str(tmp_path)))  # parent of home
    assert exc.value.status_code == 403


def test_descend_reports_parent(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "a" / "b").mkdir(parents=True)
    monkeypatch.setattr(agent_fs, "_HOME", home.resolve())

    import asyncio

    res = asyncio.run(agent_fs.list_dirs(path=str(home / "a")))
    assert res["parent"] == str(home.resolve())
    assert [d["name"] for d in res["dirs"]] == ["b"]
