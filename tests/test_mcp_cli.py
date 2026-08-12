from __future__ import annotations

import json
import sys
from pathlib import Path

from cli.mcp_cli import run


def _json_output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_mcp_cli_manages_the_new_top_level_without_storing_secrets(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))

    assert (
        run(
            [
                "add",
                "fixture",
                "--credential-env",
                "OPENROUTER_API_KEY=openrouter",
                "--approval",
                "writes",
                "--json",
                "--command",
                "python3",
                "server.py",
            ]
        )
        == 0
    )
    added = _json_output(capsys)
    assert added["servers"][0]["credentialEnvKeys"] == ["OPENROUTER_API_KEY"]

    config = json.loads((home / "deepcode_config.json").read_text(encoding="utf-8"))
    assert "tools" not in config
    stored = config["mcpServers"]["fixture"]
    assert stored["credentialEnv"]["OPENROUTER_API_KEY"] == {
        "credentialRef": "provider:openrouter"
    }

    assert run(["list", "--json"]) == 0
    listed = _json_output(capsys)
    serialized = json.dumps(listed)
    assert "OPENROUTER_API_KEY" in serialized
    assert "apiKey" not in serialized

    assert run(["remove", "fixture", "--json"]) == 0
    assert _json_output(capsys)["servers"] == []


def test_project_mcp_cli_requires_explicit_trust_for_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))

    arguments = [
        "add",
        "project-tools",
        "--scope",
        "project",
        "--workspace",
        str(workspace),
        "--json",
        "--command",
        "python3",
    ]
    assert run(arguments) == 2
    assert "trusted" in capsys.readouterr().err

    arguments.insert(arguments.index("--json"), "--trust")
    assert run(arguments) == 0
    assert _json_output(capsys)["servers"][0]["source"] == "project"
    project_config = json.loads(
        (workspace / "deepcode_config.json").read_text(encoding="utf-8")
    )
    assert "project-tools" in project_config["mcpServers"]


def test_mcp_cli_rejects_literal_sensitive_environment_values(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))

    assert (
        run(
            [
                "add",
                "unsafe",
                "--env",
                "OPENROUTER_API_KEY=do-not-store",
                "--command",
                "python3",
            ]
        )
        == 2
    )
    assert "credentialEnv" in capsys.readouterr().err
    config_path = home / "deepcode_config.json"
    assert not config_path.exists() or "do-not-store" not in config_path.read_text(
        encoding="utf-8"
    )


def test_mcp_cli_adds_enables_and_really_probes_mcp_servers(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))

    assert run(["presets", "--json"]) == 0
    presets = _json_output(capsys)
    assert len(presets["presets"]) == 16
    assert any(preset["id"] == "context7" for preset in presets["presets"])

    assert run(["add", "context7", "--json"]) == 0
    configured = _json_output(capsys)
    context7 = next(
        server for server in configured["servers"] if server["name"] == "context7"
    )
    assert context7["enabled"] is False
    assert run(["enable", "context7", "--json"]) == 0
    enabled = _json_output(capsys)
    assert (
        next(server for server in enabled["servers"] if server["name"] == "context7")[
            "enabled"
        ]
        is True
    )
    assert run(["disable", "context7", "--json"]) == 0
    _json_output(capsys)

    assert (
        run(
            [
                "add",
                "fixture",
                "--json",
                "--command",
                sys.executable,
                str(fixture),
            ]
        )
        == 0
    )
    _json_output(capsys)
    added = json.loads((home / "deepcode_config.json").read_text(encoding="utf-8"))
    assert added["mcpServers"]["fixture"]["enabled"] is False
    assert run(["test", "fixture", "--json"]) == 0
    probe = _json_output(capsys)
    assert probe["ok"] is True
    assert (probe["toolCount"], probe["resourceCount"], probe["promptCount"]) == (
        3,
        1,
        1,
    )


def test_mcp_cli_can_explicitly_enable_a_new_custom_server(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))

    assert run(["add", "fixture", "--enable", "--command", "python3"]) == 0
    capsys.readouterr()
    config = json.loads(
        (tmp_path / "home" / "deepcode_config.json").read_text(encoding="utf-8")
    )
    assert config["mcpServers"]["fixture"]["enabled"] is True


def test_mcp_cli_returns_failure_for_a_failed_real_probe(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    assert run(["add", "broken", "--command", "definitely-not-a-command"]) == 0
    capsys.readouterr()

    assert run(["test", "broken", "--json"]) == 1
    assert _json_output(capsys)["ok"] is False
