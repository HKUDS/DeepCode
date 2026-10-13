"""Top-level ``deepcode`` dispatch."""

from __future__ import annotations

import subprocess
import sys

import pytest

import deepcode


@pytest.mark.parametrize(
    "argv", [["test", "rice"], ["test", "rice", "--fast"], ["test"]]
)
def test_removed_paper_test_command_points_at_paper2code(
    monkeypatch, capsys, tmp_path, argv
):
    """`deepcode test` ran a test_paper.py that never shipped; it must say so
    instead of failing inside a subprocess."""

    monkeypatch.setattr(deepcode, "_bootstrap_logging", lambda: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["deepcode", *argv])
    launched = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a))

    with pytest.raises(SystemExit) as exit:
        deepcode.main()

    assert exit.value.code == 1
    assert launched == []
    err = capsys.readouterr().err
    assert "Paper2Code" in err
    assert "deepcode desktop" in err and "deepcode web" in err


def test_help_does_not_advertise_the_removed_paper_test(monkeypatch, capsys):
    monkeypatch.setattr(deepcode, "_bootstrap_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["deepcode", "--help"])

    deepcode.main()

    assert "deepcode test" not in capsys.readouterr().out
