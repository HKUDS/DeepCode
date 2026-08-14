"""The CLI installs its own log sinks before anything can log.

Reported in #167: loguru ships a stderr sink at DEBUG level, and nothing on
the CLI path ever replaced it. Routine internals therefore reached the user's
terminal — most visibly the "config layer absent" line `load_config` emits in
any directory without a project-level `deepcode_config.json`.

The ordering is the subtle part: reading the config is itself one of the
things that logs, so a quiet default has to be installed first.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import deepcode  # noqa: E402
from core.observability import shutdown_logging  # noqa: E402


def sink_levels() -> list[int]:
    return [handler.levelno for handler in logger._core.handlers.values()]  # noqa: SLF001


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    shutdown_logging()


def test_bootstrap_replaces_the_default_debug_sink(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPCODE_LOG_LEVEL", raising=False)
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    deepcode._bootstrap_logging()

    # 10 is loguru's DEBUG. Leaving it in place is what put internals on the
    # terminal; INFO (20) or quieter is the contract here.
    assert sink_levels(), "bootstrap must leave at least one sink installed"
    assert min(sink_levels()) >= 20


def test_env_override_wins_without_any_config_file(monkeypatch, tmp_path):
    """`DEEPCODE_LOG_LEVEL` has to work before a config exists — it is the
    escape hatch for debugging config loading itself."""

    monkeypatch.setenv("DEEPCODE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    deepcode._bootstrap_logging()

    assert min(sink_levels()) <= 10


def test_configured_level_is_honoured(monkeypatch, tmp_path):
    """`logger.level` in the config had no consumer at all before this."""

    home = tmp_path / "home"
    home.mkdir()
    (home / "deepcode_config.json").write_text(
        '{"logger": {"level": "warning", "transports": ["console"]}}',
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPCODE_LOG_LEVEL", raising=False)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    deepcode._bootstrap_logging()

    assert min(sink_levels()) >= 30  # WARNING


def test_an_unreadable_config_does_not_break_startup(monkeypatch, tmp_path):
    """Logging must survive a broken config; reporting it is the
    subcommand's job, and it needs working sinks to do that."""

    home = tmp_path / "home"
    home.mkdir()
    (home / "deepcode_config.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.delenv("DEEPCODE_LOG_LEVEL", raising=False)
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    deepcode._bootstrap_logging()

    assert sink_levels()


def test_main_bootstraps_before_dispatching(monkeypatch, capsys):
    """The call belongs in the one dispatcher every subcommand goes through,
    not repeated in each entrypoint."""

    called: list[bool] = []
    monkeypatch.setattr(deepcode, "_bootstrap_logging", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["deepcode", "--version"])

    deepcode.main()

    assert called == [True]
