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


def test_mcp_cli_tests_every_enabled_server(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    assert (
        run(
            [
                "add",
                "fixture",
                "--enable",
                "--command",
                sys.executable,
                str(fixture),
            ]
        )
        == 0
    )
    assert run(["add", "broken", "--enable", "--command", "not-a-command"]) == 0
    capsys.readouterr()

    assert run(["test", "--all", "--json"]) == 1
    report = _json_output(capsys)
    assert report["ok"] is False
    assert (report["total"], report["failed"]) == (2, 1)
    status = {server["name"]: server["ok"] for server in report["servers"]}
    assert status == {"fixture": True, "broken": False}


def test_mcp_cli_test_all_skips_disabled_servers(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    assert (
        run(
            [
                "add",
                "fixture",
                "--enable",
                "--command",
                sys.executable,
                str(fixture),
            ]
        )
        == 0
    )
    assert run(["add", "broken", "--command", "not-a-command"]) == 0
    capsys.readouterr()

    assert run(["test", "--all", "--json"]) == 0
    report = _json_output(capsys)
    assert (report["total"], report["failed"]) == (1, 0)
    assert [server["name"] for server in report["servers"]] == ["fixture"]


def test_mcp_cli_test_all_reports_a_blocked_project_server(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    trusted = tmp_path / "trusted"
    untrusted = tmp_path / "untrusted"
    trusted.mkdir()
    untrusted.mkdir()
    server = ["--enable", "--command", sys.executable, str(fixture)]
    assert run(["add", "fixture", *server]) == 0
    project = ["--scope", "project", "--workspace", str(trusted), "--trust"]
    assert run(["add", "project-tools", *project, *server]) == 0
    # The same project entry in a workspace nobody has trusted.
    (untrusted / "deepcode_config.json").write_text(
        (trusted / "deepcode_config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert run(["test", "--all", "--workspace", str(untrusted), "--json"]) == 1
    report = _json_output(capsys)
    servers = {server["name"]: server for server in report["servers"]}
    assert (report["total"], report["failed"]) == (2, 1)
    assert servers["fixture"]["ok"] is True
    assert servers["project-tools"]["ok"] is False
    assert "trusted" in servers["project-tools"]["error"]


def test_mcp_cli_test_all_reports_every_server_when_probing_raises(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    server = ["--enable", "--command", sys.executable, str(fixture)]
    assert run(["add", "first", *server]) == 0
    assert run(["add", "second", *server]) == 0
    # One malformed entry makes the resolver reject the whole user layer, so
    # every probe raises; each server is still reported and the run finishes.
    config_path = home / "deepcode_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcpServers"]["broken"] = "not an object"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    capsys.readouterr()

    assert run(["test", "--all", "--json"]) == 1
    report = _json_output(capsys)
    assert (report["total"], report["failed"]) == (3, 3)
    assert [server["name"] for server in report["servers"]] == [
        "broken",
        "first",
        "second",
    ]
    assert all(server["error"] for server in report["servers"])


def test_mcp_cli_test_requires_a_name_or_all(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DEEPCODE_HOME", str(tmp_path / "home"))
    assert run(["test"]) == 2
    assert "exactly one" in capsys.readouterr().err


def test_mcp_list_names_concerns_and_states_default_posture_once(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.delenv("DEEPCODE_TEST_UNSET_TOKEN", raising=False)
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "plain": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["plain.py"],
                    },
                    "scoped": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["scoped.py"],
                        "enabledTools": ["search"],
                        "approvalMode": "prompt",
                    },
                    "credentialed": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["cred.py"],
                        "credentialEnv": {
                            "DEMO_API_KEY": {"credentialRef": "provider:openrouter"}
                        },
                        "requiredEnvVars": ["DEEPCODE_TEST_UNSET_TOKEN"],
                    },
                    "retired": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["old.py"],
                        "enabled": False,
                    },
                    # ``type`` is required, so this row never parses.
                    "broken": {"command": "python3", "args": ["broken.py"]},
                }
            }
        ),
        encoding="utf-8",
    )

    assert run(["list"]) == 0
    header, *rows, summary = capsys.readouterr().out.splitlines()
    assert header.split() == ["CONFIG", "AUTH", "RUNTIME", "SCOPE", "NAME", "CONCERNS"]
    concerns: dict[str, str] = {}
    for row in rows:
        fields = row.split(maxsplit=5)
        concerns[fields[4]] = fields[5]
    assert concerns == {
        "broken": "-",
        "credentialed": "missing-env, sends-credential",
        "plain": "-",
        "retired": "disabled",
        "scoped": "-",
    }
    # ``plain`` inherits both permissive defaults while ``scoped`` opts out of
    # both, so neither adds a per-row label and the defaults are counted once.
    # ``broken`` never parsed: the defaults on its row were never configured, so
    # it stays out of the counts.
    assert summary == (
        "Default posture among 3 enabled servers: "
        "2 expose all tools, 2 add no MCP approval gate."
    )


def test_mcp_list_flags_unpinned_launcher_packages(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    (home / "deepcode_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "npx_tag": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "left-pad@latest"],
                    },
                    "npx_noversion": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    },
                    "npx_pinned": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "left-pad@1.2.3"],
                    },
                    "npx_flags_only": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["--version"],
                    },
                    "npx_local_file": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["./server.js"],
                    },
                    "npx_windows": {
                        "type": "stdio",
                        "command": "C:\\Program Files\\nodejs\\npx.cmd",
                        "args": ["-y", "left-pad"],
                    },
                    "uvx_noversion": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["mcp-server-time"],
                    },
                    "uvx_pinned": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["mcp-server-time==0.3.0"],
                    },
                    "python": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["server.py"],
                    },
                    "npx_cache_value": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["--cache", "/tmp/c", "-y", "left-pad"],
                    },
                    "npx_package_option_pinned": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["--package", "left-pad@1.2.3", "-y"],
                    },
                    "npx_package_short_unpinned": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "-p", "left-pad", "cowsay@2.0.0"],
                    },
                    "npx_major_only": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "left-pad@1"],
                    },
                    "npx_minor_wild": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "left-pad@1.x"],
                    },
                    "npx_prerelease": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "left-pad@1.2.3-beta.1"],
                    },
                    "uvx_python_option": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--python", "3.12", "mcp-server-time==0.3.0"],
                    },
                    "uvx_python_short": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["-p", "3.12", "mcp-server-time==0.3.0"],
                    },
                    "uvx_with_option": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--with", "httpx", "mcp-server-time==0.3.0"],
                    },
                    "uvx_from_option": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--from", "mcp-server-time==0.3.0"],
                    },
                    "uvx_from_local": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["--from", ".", "my-mcp"],
                    },
                    "npx_package_local": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-p", "./local-pkg", "cmd"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    assert run(["list"]) == 0
    header, *rows, summary = capsys.readouterr().out.splitlines()
    assert header.split() == ["CONFIG", "AUTH", "RUNTIME", "SCOPE", "NAME", "CONCERNS"]
    concerns: dict[str, str] = {}
    for row in rows:
        fields = row.split(maxsplit=5)
        concerns[fields[4]] = fields[5]
    # The posture line counts only enabled servers whose configuration
    # resolves, and command availability differs by host (a Windows path or
    # ``uvx`` may be absent here), so derive the denominator from the rows.
    readable = sum(
        1 for row in rows if row.split(maxsplit=1)[0] not in {"invalid", "disabled"}
    )
    assert readable >= 1
    assert summary.startswith(f"Default posture among {readable} enabled servers")
    # ``npx``/``uvx`` resolve their package argument from the registry on
    # every start, so a missing version or a floating tag is the concern;
    # a pinned version, a local file, a non-launcher command and a command
    # line without a package are not. The package is read from the
    # launcher's own option when one names it (a local path there is not a
    # package either), other options' values are
    # skipped rather than scanned, and npm's partial versions (``@1``,
    # ``@1.x``) are ranges -- only a full ``x.y.z``, with an optional
    # pre-release suffix, counts as pinned there.
    assert concerns == {
        "npx_tag": "unpinned-package",
        "npx_noversion": "unpinned-package",
        "npx_pinned": "-",
        "npx_flags_only": "-",
        "npx_local_file": "-",
        "npx_windows": "unpinned-package",
        "npx_cache_value": "unpinned-package",
        "npx_package_option_pinned": "-",
        "npx_package_short_unpinned": "unpinned-package",
        "npx_major_only": "unpinned-package",
        "npx_minor_wild": "unpinned-package",
        "npx_prerelease": "-",
        "uvx_noversion": "unpinned-package",
        "uvx_pinned": "-",
        "uvx_python_option": "-",
        "uvx_python_short": "-",
        "uvx_with_option": "-",
        "uvx_from_option": "-",
        "uvx_from_local": "-",
        "npx_package_local": "-",
        "python": "-",
    }
