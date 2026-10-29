"""`deepcode hooks status|approve` over the shared application state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.hooks_cli import run
from core.application import DeepCodeApplication
from core.domain.project import TrustState


def _declare(workspace: Path, command: str) -> None:
    path = workspace / ".deepcode" / "hooks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _set_trust(workspace: Path, trusted: bool) -> None:
    application = DeepCodeApplication.open(run_automation_scheduler=False)
    try:
        application.projects.add(
            str(workspace),
            trust_state=TrustState.TRUSTED if trusted else TrustState.UNTRUSTED,
        )
    finally:
        application.close()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _status(workspace: Path, capsys) -> dict:
    assert run(["status", "--workspace", str(workspace), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_status_and_approve_follow_the_declarations(workspace: Path, capsys) -> None:
    _set_trust(workspace, True)  # trusted before it declared any hooks
    assert _status(workspace, capsys)["projectHooks"]["status"] == "none"

    _declare(workspace, "python3 check.py")
    listed = _status(workspace, capsys)
    assert listed["projectHooks"]["status"] == "unapproved"
    assert [hook["command"] for hook in listed["hooks"]] == ["python3 check.py"]

    assert run(["status", "--workspace", str(workspace)]) == 0
    text = capsys.readouterr().out
    assert "Project hooks: unapproved (changed since they were approved)" in text
    assert "PreToolUse [Bash]: python3 check.py" in text
    assert listed["projectHooks"]["digest"] in text

    assert run(["approve", "--workspace", str(workspace), "--yes", "--json"]) == 0
    approved = json.loads(capsys.readouterr().out)["projectHooks"]
    assert approved["status"] == "approved"
    assert approved["approvedDigest"] == listed["projectHooks"]["digest"]

    _declare(workspace, "python3 other.py")
    assert _status(workspace, capsys)["projectHooks"]["status"] == "unapproved"


def test_approve_asks_before_approving(
    workspace: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_trust(workspace, True)
    _declare(workspace, "python3 check.py")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert run(["approve", "--workspace", str(workspace)]) == 1
    assert "cancelled" in capsys.readouterr().out
    assert _status(workspace, capsys)["projectHooks"]["status"] == "unapproved"

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert run(["approve", "--workspace", str(workspace)]) == 0
    assert "approved" in capsys.readouterr().out
    assert _status(workspace, capsys)["projectHooks"]["status"] == "approved"


def test_approve_without_a_terminal_requires_yes(
    workspace: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_trust(workspace, True)
    _declare(workspace, "python3 check.py")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert run(["approve", "--workspace", str(workspace)]) == 2
    assert "--yes" in capsys.readouterr().err


def test_approve_refuses_an_untrusted_project(workspace: Path, capsys) -> None:
    _declare(workspace, "python3 check.py")
    _set_trust(workspace, False)

    assert run(["approve", "--workspace", str(workspace), "--yes"]) == 2
    captured = capsys.readouterr()
    assert "not trusted" in captured.err
    assert "python3 check.py" in captured.out  # it still showed what was declared

    status = _status(workspace, capsys)["projectHooks"]
    assert status["status"] == "unapproved"
    assert status["approvedDigest"] is None
