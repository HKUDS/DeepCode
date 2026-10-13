"""Tests for credential-shaped environment scrubbing.

The chain this closes: ``.env`` is loaded into the harness process by several
MCP servers, every spawned child inherits that environment, and any command
whose stdout becomes a tool result forwards the value into the next outbound
request — where a third-party relay reads it in plaintext. A single ``env``
call used to be enough to close that loop.
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

from core.harness.env_sanitize import (
    FULL_ENV_ENV_VAR,
    SCRUB_ENV_VAR,
    SENSITIVE_ENV_PATTERN,
    child_env,
    full_env_requested,
    scrubbed_parent_env,
)

# The names the local .env actually defines. Every one must be dropped.
_LOCAL_CREDENTIAL_NAMES = [
    "NVIDIA_API_KEY",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "TUSHARE_TOKEN",
    "XIAOMI_TOKEN_PLAN_CN_API_KEY",
    "ZHIPU_API_KEY",
    "SILICONFLOW_API_KEY",
    "SCNET_TP_API_KEY",
    "AGNES_API_KEY",
    "DEEPSEEK_API_KEY",
    "MY_PASSWORD",
    "CLIENT_SECRET",
]


@pytest.mark.parametrize("name", _LOCAL_CREDENTIAL_NAMES)
def test_credential_shaped_names_are_matched(name):
    assert SENSITIVE_ENV_PATTERN.search(name) is not None


@pytest.mark.parametrize("name", _LOCAL_CREDENTIAL_NAMES)
def test_credential_shaped_names_are_dropped(monkeypatch, name):
    monkeypatch.setenv(name, "sensitive-value")
    env = scrubbed_parent_env()
    assert name not in env


def test_child_still_runs(monkeypatch):
    """The scrub must not break ordinary execution."""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sensitive-value")
    env = scrubbed_parent_env()
    if "PATH" in __import__("os").environ:
        assert "PATH" in env
    assert "HOME" in env or "USERPROFILE" in env


def test_extra_env_merges_after_the_scrub(monkeypatch):
    """A deliberate forward wins; an ambient credential does not."""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "ambient")
    monkeypatch.setenv("UNRELATED", "ambient")
    env = scrubbed_parent_env(
        {"DEEPSEEK_API_KEY": "deliberate", "FORWARDED_TOKEN": "on purpose"}
    )
    assert env["DEEPSEEK_API_KEY"] == "deliberate"
    assert env["FORWARDED_TOKEN"] == "on purpose"
    assert env["UNRELATED"] == "ambient"


def test_force_full_keeps_everything(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    env = scrubbed_parent_env(force_full=True)
    assert env["DEEPSEEK_API_KEY"] == "present"


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_var_waiver(monkeypatch, value):
    monkeypatch.setenv(FULL_ENV_ENV_VAR, value)
    assert full_env_requested() is True
    env = scrubbed_parent_env()
    assert "DEEPSEEK_API_KEY" not in env  # unless it was actually set
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    assert scrubbed_parent_env()["DEEPSEEK_API_KEY"] == "present"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_waiver_off_by_default(monkeypatch, value):
    monkeypatch.setenv(FULL_ENV_ENV_VAR, value)
    assert full_env_requested() is False


def test_external_backend_reexport_still_works():
    """The function moved modules; existing importers must keep working."""

    from core.harness.agents.external_backend import (
        SENSITIVE_ENV_PATTERN as reexported_pattern,
    )
    from core.harness.agents.external_backend import (
        scrubbed_parent_env as reexported,
    )

    assert reexported is scrubbed_parent_env
    assert reexported_pattern is SENSITIVE_ENV_PATTERN


@pytest.mark.parametrize(
    "name",
    [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_SHARED_CREDENTIALS_FILE",
        "NPM_AUTH",
        "OAUTH_CLIENT",
        "PROXY_AUTHORIZATION",
    ],
)
def test_credential_and_auth_names_are_matched(name):
    assert SENSITIVE_ENV_PATTERN.search(name) is not None


@pytest.mark.parametrize(
    "name", ["GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "SSH_AUTH_SOCK", "PATH"]
)
def test_identity_and_agent_socket_names_survive_the_scrub(name):
    """Commits keep their author and git-over-ssh keeps its agent."""

    assert SENSITIVE_ENV_PATTERN.search(name) is None


@pytest.fixture
def clean_switches(monkeypatch):
    monkeypatch.delenv(FULL_ENV_ENV_VAR, raising=False)
    monkeypatch.delenv(SCRUB_ENV_VAR, raising=False)


def test_child_env_drops_deepcode_provider_keys_by_default(monkeypatch, clean_switches):
    """DeepCode's own model keys never reach an agent command; the user's
    tool tokens still do."""

    monkeypatch.setenv("OPENAI_API_KEY", "deepcode-model-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepcode-model-key")
    monkeypatch.setenv("GH_TOKEN", "user-tool-token")
    env = child_env()
    assert "OPENAI_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" not in env
    assert env["GH_TOKEN"] == "user-tool-token"


def test_child_env_drops_a_configured_connection_key_variable(
    monkeypatch, clean_switches
):
    home = Path(os.environ["DEEPCODE_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "providers": {
                    "profiles": {
                        "relay": {
                            "template": "custom",
                            "apiBase": "https://relay.example/v1",
                            "apiKeyEnv": "MY_RELAY_SECRET_VALUE",
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("MY_RELAY_SECRET_VALUE", "deepcode-model-key")
    assert "MY_RELAY_SECRET_VALUE" not in child_env()


def _write_user_config(payload: dict) -> None:
    home = Path(os.environ["DEEPCODE_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "deepcode_config.json").write_text(json.dumps(payload))


def test_child_env_drops_variables_the_provider_config_references(
    monkeypatch, clean_switches
):
    """``"apiKey": "${NAME}"`` reads a model key from the environment as surely
    as ``apiKeyEnv`` does, and so does a header carrying one."""

    _write_user_config(
        {
            "providers": {
                "deepseek": {"apiKey": "${MY_DS_KEY}"},
                "profiles": {
                    "relay": {
                        "template": "custom",
                        "apiBase": "https://relay.example/v1",
                        "extraHeaders": {"Authorization": "Bearer ${RELAY_BEARER}"},
                    }
                },
            },
            "dictation": {
                "endpoint": "https://speech.example/v1",
                "model": "parakeet",
                "apiKeyEnv": "MY_SPEECH_KEY",
            },
        }
    )
    for name in ("MY_DS_KEY", "RELAY_BEARER", "MY_SPEECH_KEY", "UNRELATED_SETTING"):
        monkeypatch.setenv(name, "value")
    env = child_env()
    assert not {"MY_DS_KEY", "RELAY_BEARER", "MY_SPEECH_KEY"} & set(env)
    assert env["UNRELATED_SETTING"] == "value"


def test_one_unset_reference_does_not_release_the_other_keys(
    monkeypatch, clean_switches
):
    """An unset ``${NAME}`` makes the config fail to load; the names it does
    reference must still be withheld."""

    _write_user_config(
        {
            "providers": {
                "openai": {"apiKey": "${NOT_SET_ANYWHERE}"},
                "profiles": {
                    "relay": {
                        "template": "custom",
                        "apiBase": "https://relay.example/v1",
                        "apiKeyEnv": "MY_RELAY_SECRET_VALUE",
                    }
                },
            }
        }
    )
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    monkeypatch.setenv("MY_RELAY_SECRET_VALUE", "deepcode-model-key")
    assert "MY_RELAY_SECRET_VALUE" not in child_env()


def test_child_env_survives_an_unreadable_config(monkeypatch, clean_switches):
    home = Path(os.environ["DEEPCODE_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "deepcode_config.json").write_text("{not json")
    monkeypatch.setenv("OPENAI_API_KEY", "deepcode-model-key")
    env = child_env()
    assert "OPENAI_API_KEY" not in env
    assert "PATH" in env


def test_full_env_waiver_still_hands_over_provider_keys(monkeypatch):
    monkeypatch.setenv(FULL_ENV_ENV_VAR, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "deepcode-model-key")
    assert child_env()["OPENAI_API_KEY"] == "deepcode-model-key"


def test_child_env_drops_deepcode_own_python_environment(
    monkeypatch, clean_switches, tmp_path
):
    """A service started from DeepCode's checkout or virtualenv must not point
    the project's Python at DeepCode's packages (it ships top-level ``tools``
    and ``utils``)."""

    project_lib = tmp_path / "lib"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT), str(project_lib)]))
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    env = child_env()
    assert env["PYTHONPATH"] == str(project_lib)
    assert "VIRTUAL_ENV" not in env

    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    env = child_env()
    assert "PYTHONPATH" not in env
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_external_agents_lose_deepcode_python_environment_too(
    monkeypatch, clean_switches
):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    assert "PYTHONPATH" not in scrubbed_parent_env()
    assert scrubbed_parent_env(force_full=True)["PYTHONPATH"] == str(ROOT)
