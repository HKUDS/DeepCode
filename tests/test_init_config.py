"""Tests for safe, workspace-independent ``deepcode init``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.init_config import run
from core.config import _DEFAULT_CONFIG_FILENAME, home_config_path

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


def test_default_init_never_copies_workspace_config(isolated, capsys):
    _home, project = isolated
    _write(project, _KEYED)
    rc = run([])
    assert rc == 0
    dest = home_config_path()
    assert dest.is_file()
    assert json.loads(dest.read_text()) == {"security": {"accessPreset": "ask"}}
    out = capsys.readouterr().out
    assert "sk-real" not in out
    assert "deepcode provider set" in out


def test_posix_config_is_user_only(isolated):
    if os.name != "posix":
        pytest.skip("permission bits are posix-only")
    run([])
    assert (isolated[0].stat().st_mode & 0o777) == 0o700
    assert (home_config_path().stat().st_mode & 0o777) == 0o600


def test_idempotent_second_run_does_not_clobber(isolated, capsys):
    run([])
    destination = home_config_path()
    destination.write_text('{"custom": "keep"}\n', encoding="utf-8")
    rc = run([])
    assert rc == 0
    assert "Already initialized" in capsys.readouterr().out
    assert json.loads(destination.read_text()) == {"custom": "keep"}


def test_force_backs_up_then_reseeds_from_explicit_source(isolated, tmp_path):
    first = tmp_path / "first.json"
    first.write_text(json.dumps(_KEYED), encoding="utf-8")
    second_payload = {"agents": {"defaults": {"model": "example/new"}}}
    second = tmp_path / "second.json"
    second.write_text(json.dumps(second_payload), encoding="utf-8")
    run(["--from", str(first)])

    rc = run(["--force", "--from", str(second)])

    assert rc == 0
    dest = home_config_path()
    assert json.loads(dest.read_text()) == second_payload
    backup = dest.with_suffix(dest.suffix + ".bak")
    assert json.loads(backup.read_text())["providers"]["openai"]["apiKey"] == "sk-real"
    if os.name == "posix":
        assert (backup.stat().st_mode & 0o777) == 0o600


def test_from_explicit_path(isolated, tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    src = _write(custom, {"providers": {"openai": {"apiKey": "sk-from"}}})
    rc = run(["--from", str(src)])
    assert rc == 0
    assert (
        json.loads(home_config_path().read_text())["providers"]["openai"]["apiKey"]
        == "sk-from"
    )


def test_from_missing_path_errors(isolated, capsys):
    rc = run(["--from", str(isolated[0] / "nope.json")])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().out


def test_default_init_does_not_depend_on_repository_template(isolated, capsys):
    rc = run([])
    assert rc == 0
    assert home_config_path().is_file()
    out = capsys.readouterr().out
    assert "safe defaults" in out


def test_invalid_explicit_json_is_rejected_without_partial_file(
    isolated,
    tmp_path,
    capsys,
):
    source = tmp_path / "broken.json"
    source.write_text("{", encoding="utf-8")

    assert run(["--from", str(source)]) == 1

    assert not home_config_path().exists()
    assert "invalid JSON" in capsys.readouterr().out
