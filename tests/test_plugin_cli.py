from __future__ import annotations

import json
from pathlib import Path

from cli.plugin_cli import run
from core.plugins.formats.agent_plugins_v1 import AGENT_PLUGIN_SCHEMA


def _write_plugin(root: Path) -> Path:
    skill = root / "skills" / "plugin-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: plugin-review\ndescription: Review code\n---\nReview it.\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": AGENT_PLUGIN_SCHEMA,
                "name": "review-tools",
                "version": "1.0.0",
                "description": "Review tools",
            }
        ),
        encoding="utf-8",
    )
    return root


def _output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_plugin_cli_manages_the_shared_local_registry(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    plugin = _write_plugin(tmp_path / "plugin")

    assert run(["--json", "add", str(plugin)]) == 0
    assert _output(capsys)["plugins"][0]["status"] == "active"
    assert run(["--json", "disable", "review-tools"]) == 0
    assert _output(capsys)["plugins"][0]["status"] == "disabled"
    assert run(["--json", "enable", "review-tools"]) == 0
    assert _output(capsys)["plugins"][0]["status"] == "active"
    assert run(["--json", "remove", "review-tools", "--yes"]) == 0
    assert _output(capsys)["removed"] is True
    assert plugin.is_dir()


def test_skill_cli_lists_registered_plugin_skills(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from cli.skill_cli import run as skill_run

    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    plugin = _write_plugin(tmp_path / "plugin")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert run(["--json", "add", str(plugin)]) == 0
    capsys.readouterr()

    assert skill_run(["--workspace", str(workspace), "--json", "list"]) == 0
    inventory = _output(capsys)
    plugin_skill = next(
        skill for skill in inventory["skills"] if skill["name"] == "plugin-review"
    )
    assert plugin_skill["source"].startswith("custom:plg_")
