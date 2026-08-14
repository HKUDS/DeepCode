from __future__ import annotations

import json
from pathlib import Path

from cli.skill_cli import run


def _write_skill(root: Path, name: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: A CLI-managed workflow\n"
        "---\n"
        "Follow the verified workflow.\n",
        encoding="utf-8",
    )
    return directory


def _json_output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_skill_cli_manages_the_shared_catalog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = _write_skill(tmp_path / "source", "review")
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "deepcode-home"))
    common = ["--workspace", str(workspace), "--json"]

    assert run([*common, "import", str(source), "--scope", "project"]) == 0
    imported = _json_output(capsys)
    skill_id = imported["id"]
    assert imported["status"] == "active"

    assert run([*common, "list"]) == 0
    inventory = _json_output(capsys)
    assert inventory["skills"][0]["id"] == skill_id

    assert run([*common, "disable", skill_id, "--scope", "project"]) == 0
    assert _json_output(capsys)["status"] == "disabled"
    assert run([*common, "enable", skill_id, "--scope", "project"]) == 0
    assert _json_output(capsys)["status"] == "active"

    assert run([*common, "remove", skill_id, "--yes"]) == 0
    assert _json_output(capsys)["removed"] is True
    assert run([*common, "list"]) == 0
    assert all(skill["id"] != skill_id for skill in _json_output(capsys)["skills"])


def test_skill_cli_reports_unknown_skill_without_traceback(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "deepcode-home"))

    result = run(
        [
            "--workspace",
            str(workspace),
            "--json",
            "show",
            "missing",
        ]
    )

    assert result == 2
    assert "Skill not found" in _json_output(capsys)["error"]
