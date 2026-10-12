"""A logged exception keeps its traceback in every log DeepCode writes.

The console format used to stop at the message, and the JSONL files stored
``repr`` of loguru's exception tuple, whose traceback is dropped when the
record is queued. Every ``logger.exception`` lost the place it failed.
"""

from __future__ import annotations

import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from loguru import logger

from core.config import LoggerConfig
from core.observability import setup_logging


def _fail_deep_inside() -> None:
    token = "sk-" + "never-in-a-log"
    raise ValueError(f"bad value of length {len(token)}")


def _log_failure(via_stdlib: bool = False) -> None:
    try:
        _fail_deep_inside()
    except ValueError:
        if via_stdlib:
            logging.getLogger("tracebacks.test").exception("stdlib saw it fail")
        else:
            logger.exception("loguru saw it fail")


def _assert_traceback(text: str) -> None:
    assert "Traceback (most recent call last)" in text
    assert "_fail_deep_inside" in text
    assert "ValueError: bad value of length 17" in text
    # Variable values stay out: a local can hold a credential.
    assert "never-in-a-log" not in text


@pytest.fixture(autouse=True)
def _restore_logging(tmp_path: Path):
    yield
    setup_logging(
        LoggerConfig(transports=["console", "global_file"]),
        workspace_root=tmp_path,
        force=True,
    )


def test_the_service_log_keeps_tracebacks_without_blank_lines(tmp_path: Path):
    path = tmp_path / "service.log"
    handler = RotatingFileHandler(path, encoding="utf-8")
    setup_logging(
        LoggerConfig(transports=["console"]), force=True, console_sink=handler
    )
    logger.info("before")
    _log_failure()
    _log_failure(via_stdlib=True)
    logger.info("after")
    handler.flush()

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "" not in lines
    assert lines[0].endswith("before") and lines[-1].endswith("after")
    loguru_part = text[text.index("loguru saw it fail") : text.index("stdlib saw")]
    stdlib_part = text[text.index("stdlib saw it fail") : text.index("after")]
    _assert_traceback(loguru_part)
    _assert_traceback(stdlib_part)


def test_the_console_keeps_tracebacks(tmp_path: Path):
    sink = io.StringIO()
    setup_logging(LoggerConfig(transports=["console"]), force=True, console_sink=sink)
    _log_failure()
    text = sink.getvalue()
    assert text.splitlines()[0].endswith("loguru saw it fail")
    _assert_traceback(text)


@pytest.mark.parametrize("via_stdlib", [False, True])
def test_the_jsonl_log_keeps_tracebacks(tmp_path: Path, via_stdlib: bool):
    setup_logging(
        LoggerConfig(transports=["global_file"]),
        workspace_root=tmp_path,
        force=True,
    )
    _log_failure(via_stdlib=via_stdlib)
    logger.complete()

    rows = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("server-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    row = next(row for row in rows if row["message"].endswith("saw it fail"))
    _assert_traceback(row["exception"])
