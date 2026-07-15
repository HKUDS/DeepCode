"""Tests for ``deepcode init`` — seeding the user-level config base.

Exercises the real ``cli.init_config.run`` against isolated home/project dirs
(the user's real ``~/.deepcode`` is never touched — DEEPCODE_HOME is redirected).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.init_config import run  # noqa: E402
from core.config import _DEFAULT_CONFIG_FILENAME, home_config_path  # noqa: E402

_KEYED = {"providers": {"openai": {"apiKey": "sk-real"}}}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(project)
    return home, project


def _write(directory: Path, data: dict) -> Path:
    p = directory / _DEFAULT_CONFIG_FILENAME
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_seeds_home_from_current_project_config(isolated, capsys):
    home, project = isolated
    _write(project, _KEYED)  # a configured checkout
    rc = run([])
    assert rc == 0
    dest = home_config_path()
    assert dest.is_file()
    assert json.loads(dest.read_text())["providers"]["openai"]["apiKey"] == "sk-real"
    out = capsys.readouterr().out
    assert "run `deepcode` from ANY directory" in out  # key detected


def test_posix_config_is_user_only(isolated):
    if os.name != "posix":
        pytest.skip("permission bits are posix-only")
    _write(isolated[1], _KEYED)
    run([])
    assert (home_config_path().stat().st_mode & 0o777) == 0o600


def test_idempotent_second_run_does_not_clobber(isolated, capsys):
    home, project = isolated
    _write(project, _KEYED)
    run([])
    # change the project config; a plain re-run must NOT overwrite the home base
    _write(project, {"providers": {"openai": {"apiKey": "sk-changed"}}})
    rc = run([])
    assert rc == 0
    assert "Already configured" in capsys.readouterr().out
    assert json.loads(home_config_path().read_text())["providers"]["openai"]["apiKey"] == "sk-real"


def test_force_backs_up_then_reseeds(isolated):
    home, project = isolated
    _write(project, _KEYED)
    run([])
    _write(project, {"providers": {"openai": {"apiKey": "sk-v2"}}})
    rc = run(["--force"])
    assert rc == 0
    dest = home_config_path()
    assert json.loads(dest.read_text())["providers"]["openai"]["apiKey"] == "sk-v2"
    backup = dest.with_suffix(dest.suffix + ".bak")
    assert json.loads(backup.read_text())["providers"]["openai"]["apiKey"] == "sk-real"


def test_from_explicit_path(isolated, tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    src = _write(custom, {"providers": {"openai": {"apiKey": "sk-from"}}})
    rc = run(["--from", str(src)])
    assert rc == 0
    assert json.loads(home_config_path().read_text())["providers"]["openai"]["apiKey"] == "sk-from"


def test_from_missing_path_errors(isolated, capsys):
    rc = run(["--from", str(isolated[0] / "nope.json")])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().out


def test_falls_back_to_template_when_no_project_config(isolated, capsys):
    # No project config next to the cwd -> seed from the shipped template.
    rc = run([])
    assert rc == 0
    assert home_config_path().is_file()
    out = capsys.readouterr().out
    assert "template" in out.lower()
