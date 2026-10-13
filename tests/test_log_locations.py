"""Logs with no task or workspace of their own go to the DeepCode home.

They used to go to ``logs/`` in the current directory: every TUI session
left ``logs/server-<date>.jsonl`` in the user's project, and the service,
which runs in its install directory, wrote ``llm.jsonl`` into site-packages.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from loguru import logger

from core.config import LoggerConfig, deepcode_home
from core.observability import log_llm_call, setup_logging


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    monkeypatch.chdir(path)
    yield path
    setup_logging(LoggerConfig(transports=["console"]), force=True)


def test_the_tui_keeps_its_log_out_of_the_project(project: Path):
    from cli.tui.app import _silence_console_logging

    _silence_console_logging()
    logging.getLogger("httpx").info("HTTP Request: POST /control/rpc")
    logger.complete()

    assert not (project / "logs").exists()
    written = "".join(
        path.read_text(encoding="utf-8")
        for path in (deepcode_home() / "logs").glob("server-*.jsonl")
    )
    assert "/control/rpc" in written


def test_an_llm_call_outside_a_task_is_logged_in_the_home(project: Path):
    log_llm_call(provider="openai", model="gpt-x", phase="probe", duration_ms=1)

    assert not (project / "logs").exists()
    assert (deepcode_home() / "logs" / "llm.jsonl").is_file()
