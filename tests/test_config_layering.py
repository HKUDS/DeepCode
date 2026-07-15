"""Tests for layered config resolution (C: home base + project override).

Mirrors Codex / Claude Code: a cwd-independent user-level base at
``deepcode_home()`` (``$DEEPCODE_HOME`` or ``~/.deepcode``) is deep-merged with
an optional project-level file walked up from the cwd, which overrides the base
key by key. An explicit path bypasses the layering. This is what lets
``deepcode`` launch in *any* directory while still finding provider keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import (  # noqa: E402
    _DEFAULT_CONFIG_FILENAME,
    _deep_merge,
    _load_raw,
    deepcode_home,
    home_config_path,
    load_config,
)


def _write_config(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _DEFAULT_CONFIG_FILENAME
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """An isolated home + project dir; cwd is the project, DEEPCODE_HOME is home.

    Both start empty so a test writes only the layers it exercises — and, in
    particular, the user's real ``~/.deepcode`` is never read.
    """
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(project)
    return home, project


# -- helpers -----------------------------------------------------------------


def test_deep_merge_override_wins_and_base_preserved():
    base = {"a": 1, "nested": {"keep": "x", "swap": "old"}}
    override = {"nested": {"swap": "new"}, "b": 2}
    out = _deep_merge(base, override)
    assert out == {"a": 1, "b": 2, "nested": {"keep": "x", "swap": "new"}}
    assert base == {"a": 1, "nested": {"keep": "x", "swap": "old"}}  # not mutated


def test_deep_merge_scalar_replaces_dict():
    # A non-dict override replaces a dict base wholesale (no accidental merge).
    assert _deep_merge({"x": {"y": 1}}, {"x": 5}) == {"x": 5}


def test_load_raw_absent_is_empty(tmp_path):
    assert _load_raw(tmp_path / "nope.json") == {}


def test_load_raw_rejects_invalid_json(tmp_path):
    p = tmp_path / _DEFAULT_CONFIG_FILENAME
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        _load_raw(p)


def test_load_raw_rejects_non_object(tmp_path):
    p = tmp_path / _DEFAULT_CONFIG_FILENAME
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        _load_raw(p)


def test_deepcode_home_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "custom"))
    assert deepcode_home() == (tmp_path / "custom").resolve()
    assert home_config_path() == (tmp_path / "custom" / _DEFAULT_CONFIG_FILENAME).resolve()


def test_deepcode_home_defaults_to_dot_deepcode(monkeypatch):
    monkeypatch.delenv("DEEPCODE_HOME", raising=False)
    assert deepcode_home() == (Path.home() / ".deepcode").resolve()


# -- layered load_config -----------------------------------------------------


def test_home_base_used_when_no_project_config(layered):
    home, _project = layered
    _write_config(home, {"providers": {"openai": {"apiKey": "sk-home"}}})
    cfg = load_config()  # cwd (project) has no config → base is enough
    assert cfg.providers.openai.api_key == "sk-home"


def test_project_overrides_home_deep_merge(layered):
    home, project = layered
    _write_config(
        home,
        {
            "providers": {"openai": {"apiKey": "sk-home"}},
            "agents": {"defaults": {"model": "openai/gpt-5.4"}},
        },
    )
    _write_config(project, {"agents": {"defaults": {"model": "openai/gpt-mini"}}})
    cfg = load_config()
    # project overrides the model, but the home provider key survives the merge
    assert cfg.agents.defaults.model == "openai/gpt-mini"
    assert cfg.providers.openai.api_key == "sk-home"


def test_project_only_when_no_home(layered):
    _home, project = layered
    _write_config(project, {"providers": {"openai": {"apiKey": "sk-proj"}}})
    cfg = load_config()
    assert cfg.providers.openai.api_key == "sk-proj"


def test_explicit_path_bypasses_layering(layered, tmp_path):
    home, project = layered
    _write_config(home, {"providers": {"openai": {"apiKey": "sk-home"}}})
    _write_config(project, {"providers": {"openai": {"apiKey": "sk-proj"}}})
    explicit = _write_config(tmp_path / "elsewhere", {"providers": {"openai": {"apiKey": "sk-explicit"}}})
    cfg = load_config(config_path=explicit)
    assert cfg.providers.openai.api_key == "sk-explicit"  # neither layer consulted


def test_neither_present_returns_defaults(layered):
    # Nothing on disk in either layer → defaults, so the process still boots.
    cfg = load_config()
    assert not cfg.providers.openai.api_key
