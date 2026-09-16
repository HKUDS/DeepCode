"""Tests for credential-shaped environment scrubbing.

The chain this closes: ``.env`` is loaded into the harness process by several
MCP servers, every spawned child inherits that environment, and any command
whose stdout becomes a tool result forwards the value into the next outbound
request — where a third-party relay reads it in plaintext. A single ``env``
call used to be enough to close that loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.env_sanitize import (
    FULL_ENV_ENV_VAR,
    SENSITIVE_ENV_PATTERN,
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
