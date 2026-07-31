from __future__ import annotations

import sys

import deepcode
from app_server.dispatcher import SERVER_VERSION
from app_server.runtime_probe import verify_runtime
from core.version import __version__


def test_cli_reports_the_canonical_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["deepcode", "--version"])

    deepcode.main()

    assert capsys.readouterr().out.strip() == f"DeepCode {__version__}"


def test_runtime_surfaces_share_the_canonical_core_version() -> None:
    assert SERVER_VERSION == __version__
    assert verify_runtime()["version"] == __version__
