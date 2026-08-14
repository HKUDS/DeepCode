"""Tests for the CLI-facing config-error renderer (clean message, init hint)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.config_errors import format_config_error, is_unconfigured  # noqa: E402
from core.config import ConfigError, _DEFAULT_CONFIG_FILENAME  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(project)
    return home, project


def test_config_error_is_a_value_error():
    # Subclassing keeps every existing `except ValueError` handler/test working.
    assert issubclass(ConfigError, ValueError)


def test_unconfigured_true_when_no_layer_present(isolated):
    assert is_unconfigured() is True


def test_unconfigured_false_once_home_exists(isolated):
    home, _project = isolated
    (home / _DEFAULT_CONFIG_FILENAME).write_text(json.dumps({}), encoding="utf-8")
    assert is_unconfigured() is False


def test_message_points_at_init_when_unconfigured(isolated):
    msg = format_config_error(ConfigError("Could not match a provider for model 'x'"))
    assert "Could not match a provider" in msg
    assert "deepcode init" in msg
    assert "not configured yet" in msg


def test_message_is_terse_when_config_exists(isolated):
    _home, project = isolated
    (project / _DEFAULT_CONFIG_FILENAME).write_text(json.dumps({}), encoding="utf-8")
    msg = format_config_error(
        ConfigError("Provider 'openai' requires providers.openai.apiKey")
    )
    assert "requires providers.openai.apiKey" in msg
    assert "deepcode init" not in msg  # they have config; init is not the fix
