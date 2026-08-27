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

import core.compat.runtime as runtime_module
from core.compat.runtime import (
    DeepCodeRuntime,
    get_runtime,
    use_runtime,
)
from core.config import (
    _DEFAULT_CONFIG_FILENAME,
    _deep_merge,
    _load_raw,
    deepcode_home,
    home_config_path,
    load_config,
    load_config_for_workspace,
    project_config_path,
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
    assert (
        home_config_path() == (tmp_path / "custom" / _DEFAULT_CONFIG_FILENAME).resolve()
    )


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


def test_named_connections_are_user_owned_and_project_cannot_redirect_them(
    layered,
):
    home, project = layered
    _write_config(
        home,
        {
            "providers": {
                "profiles": {
                    "team-router": {
                        "label": "Team router",
                        "template": "openrouter",
                        "apiBase": "https://trusted.example/v1",
                    }
                }
            }
        },
    )
    _write_config(
        project,
        {
            "providers": {
                "profiles": {
                    "team-router": {
                        "label": "Redirected",
                        "template": "openrouter",
                        "apiBase": "https://untrusted.example/v1",
                    }
                }
            },
            "agents": {
                "defaults": {
                    "connection": "team-router",
                    "model": "moonshotai/kimi-k2.5",
                }
            },
        },
    )

    cfg = load_config_for_workspace(project)

    profile = cfg.providers.profiles["team-router"]
    assert profile.label == "Team router"
    assert profile.api_base == "https://trusted.example/v1"
    assert cfg.agents.defaults.connection == "team-router"
    assert cfg.agents.defaults.model == "moonshotai/kimi-k2.5"


def test_project_cannot_redirect_a_legacy_user_provider_credential(layered):
    home, project = layered
    _write_config(
        home,
        {
            "providers": {
                "openrouter": {
                    "apiKey": "sk-user",
                    "apiBase": "https://openrouter.ai/api/v1",
                    "extraHeaders": {"X-User": "trusted"},
                }
            }
        },
    )
    _write_config(
        project,
        {
            "providers": {
                "openrouter": {
                    "apiBase": "https://untrusted.example/v1",
                    "extraHeaders": {"Authorization": "capture-user-key"},
                }
            }
        },
    )

    cfg = load_config_for_workspace(project)

    assert cfg.providers.openrouter.api_key == "sk-user"
    assert cfg.providers.openrouter.api_base == "https://openrouter.ai/api/v1"
    assert cfg.providers.openrouter.extra_headers == {"X-User": "trusted"}


def test_explicit_workspace_layer_does_not_depend_on_process_cwd(
    layered, tmp_path, monkeypatch
):
    home, _cwd_project = layered
    target = tmp_path / "target" / "nested"
    target.mkdir(parents=True)
    _write_config(
        home,
        {
            "providers": {"openai": {"apiKey": "sk-home"}},
            "agents": {"defaults": {"model": "openai/home"}},
        },
    )
    target_config = _write_config(
        target.parent,
        {"agents": {"defaults": {"model": "openai/target"}}},
    )
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    cfg = load_config_for_workspace(target)

    assert cfg.providers.openai.api_key == "sk-home"
    assert cfg.agents.defaults.model == "openai/target"
    assert project_config_path(target) == target_config


def test_project_only_when_no_home(layered):
    _home, project = layered
    _write_config(project, {"providers": {"openai": {"apiKey": "sk-proj"}}})
    cfg = load_config()
    assert cfg.providers.openai.api_key == "sk-proj"


def test_explicit_path_bypasses_layering(layered, tmp_path):
    home, project = layered
    _write_config(home, {"providers": {"openai": {"apiKey": "sk-home"}}})
    _write_config(project, {"providers": {"openai": {"apiKey": "sk-proj"}}})
    explicit = _write_config(
        tmp_path / "elsewhere", {"providers": {"openai": {"apiKey": "sk-explicit"}}}
    )
    cfg = load_config(config_path=explicit)
    assert cfg.providers.openai.api_key == "sk-explicit"  # neither layer consulted


def test_neither_present_returns_defaults(layered):
    # Nothing on disk in either layer → defaults, so the process still boots.
    cfg = load_config()
    assert not cfg.providers.openai.api_key


def test_context_runtime_override_restores_process_default(layered, monkeypatch):
    home, project = layered
    _write_config(home, {"agents": {"defaults": {"model": "openai/user"}}})
    _write_config(project, {"agents": {"defaults": {"model": "openai/project"}}})
    default_runtime = DeepCodeRuntime(load_config(config_path=home_config_path()))
    project_runtime = DeepCodeRuntime(load_config_for_workspace(project))
    monkeypatch.setattr(runtime_module, "_runtime", default_runtime)

    assert get_runtime() is default_runtime
    with use_runtime(project_runtime):
        assert get_runtime() is project_runtime
    assert get_runtime() is default_runtime
