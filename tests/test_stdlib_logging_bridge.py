"""Stdlib logging records obey the loguru transport configuration.

Half the application logs through ``logging.getLogger``. Before the
bridge, those records fell through to Python's lastResort stderr handler
regardless of the configured transports — which is how a background
thread's traceback could shred an interactive TUI transcript whose
console transport was off.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

from loguru import logger as loguru_logger

from core.config import LoggerConfig
from core.observability import setup_logging


def _configure(transports: list[str], tmp_path: Path) -> None:
    setup_logging(
        LoggerConfig(transports=transports),
        workspace_root=tmp_path,
        force=True,
    )


def _restore_defaults(tmp_path: Path) -> None:
    _configure(["console", "global_file"], tmp_path)


def test_stdlib_exception_stays_off_stderr_with_file_only_transports(
    tmp_path: Path, capsys
) -> None:
    _configure(["global_file"], tmp_path)
    try:
        try:
            raise RuntimeError("relay poll blew up")
        except RuntimeError:
            logging.getLogger("core.application.event_service").exception(
                "durable event relay poll failed"
            )
        loguru_logger.complete()

        captured = capsys.readouterr()
        assert "relay poll blew up" not in captured.err
        assert "Traceback" not in captured.err

        log_files = list((tmp_path / "logs").glob("*.jsonl"))
        assert log_files, "the global file sink should have been written"
        payload = "\n".join(path.read_text(encoding="utf-8") for path in log_files)
        assert "durable event relay poll failed" in payload
        assert "relay poll blew up" in payload
    finally:
        _restore_defaults(tmp_path)


def test_stdlib_records_reach_the_console_when_configured(tmp_path: Path) -> None:
    # Symmetry: the bridge routes INTO loguru — it must not swallow records
    # when a console transport is deliberately on.
    _configure(["console"], tmp_path)
    try:
        stderr = io.StringIO()
        original = sys.stderr
        sys.stderr = stderr
        try:
            sink_id = loguru_logger.add(stderr, level="WARNING")
            logging.getLogger("bridge.test").warning("visible through loguru")
            loguru_logger.remove(sink_id)
        finally:
            sys.stderr = original
        assert "visible through loguru" in stderr.getvalue()
    finally:
        _restore_defaults(tmp_path)


def test_bridge_reports_the_real_call_site(tmp_path: Path) -> None:
    _configure(["global_file"], tmp_path)
    try:
        logging.getLogger("core.application.turn_service").warning(
            "call-site fidelity check"
        )
        loguru_logger.complete()
        payload = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (tmp_path / "logs").glob("*.jsonl")
        )
        row = next(
            json.loads(line)
            for line in payload.splitlines()
            if "call-site fidelity check" in line
        )
        # The record must appear from THIS test module, not from the
        # bridge handler or logging internals.
        origin = json.dumps(row)
        assert "test_stdlib_logging_bridge" in origin
    finally:
        _restore_defaults(tmp_path)
