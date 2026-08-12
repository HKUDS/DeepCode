from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import InvalidArgumentError, ProjectNotTrustedError
from core.domain import TrustState
from core.persistence.migrations import LATEST_SCHEMA_VERSION
from core.plugins.registry import LocalPluginRegistry
from core.plugins.resolver import resolve_plugin
from core.skills.management import LocalSkillManager
from core.skills.runtime import SkillRuntime


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_mcp_plugin(root: Path) -> Path:
    root.mkdir(parents=True)
    _write_json(
        root / "plugin.json",
        {
            "$schema": ("https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"),
            "name": "review-tools",
            "version": "1.0.0",
        },
    )
    _write_json(
        root / "mcp.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {
                "context": {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:8765/mcp",
                }
            },
        },
    )
    return root


def test_extension_inventory_matches_agent_skill_and_hook_discovery(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_dir = workspace / ".deepcode" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review a change carefully\n"
        "allowed-tools: read, grep\n"
        "---\n"
        "Inspect the change and report concrete evidence.\n",
        encoding="utf-8",
    )
    malformed = workspace / ".claude" / "skills" / "broken"
    malformed.mkdir(parents=True)
    (malformed / "SKILL.md").write_text("missing frontmatter", encoding="utf-8")
    _write_json(
        workspace / ".deepcode" / "hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 check.py",
                                "timeout": 15,
                            }
                        ],
                    }
                ]
            }
        },
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        skills = application.extensions.skills(project.id)
        review = next(skill for skill in skills.skills if skill.name == "review")
        assert review.allowed_tools == ("read", "grep")
        assert any("frontmatter" in warning for warning in skills.warnings)
        cli_record = LocalSkillManager(workspace).find(review.id)
        agent_record = SkillRuntime(workspace).catalog().get(review.id)
        assert agent_record is not None
        assert (
            (review.id, review.revision)
            == (
                cli_record.id,
                cli_record.revision,
            )
            == (agent_record.id, agent_record.revision)
        )

        detail = application.extensions.skill(project.id, "review")
        assert "concrete evidence" in detail.instructions
        assert detail.truncated is False

        hooks = application.extensions.hooks(project.id)
        assert len(hooks.hooks) == 1
        assert hooks.hooks[0].event_name == "PreToolUse"
        assert hooks.hooks[0].matcher == "Bash"
        assert hooks.hooks[0].timeout_seconds == 15
    finally:
        application.close()


def test_untrusted_project_exposes_skill_metadata_but_not_instructions(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_dir = workspace / ".deepcode" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: review\n"
        "description: Review a change carefully\n"
        "---\n"
        "PRIVATE-PROJECT-INSTRUCTIONS\n",
        encoding="utf-8",
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    try:
        inventory = application.skills.list(project.id)
        review = next(skill for skill in inventory.skills if skill.name == "review")
        with pytest.raises(ProjectNotTrustedError):
            application.skills.read(project.id, review.id)

        application.projects.update(project.id, trust_state=TrustState.TRUSTED)
        detail = application.skills.read(project.id, review.id)
        assert "PRIVATE-PROJECT-INSTRUCTIONS" in detail.instructions
    finally:
        application.close()


def test_mcp_inventory_redacts_secrets_and_mutates_explicit_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(workspace)
    _write_json(
        home / "deepcode_config.json",
        {
            "mcpServers": {
                "demo": {
                    "type": "stdio",
                    "command": "python3",
                    "args": ["server.py", "--token", "secret-value"],
                    "credentialEnv": {
                        "API_TOKEN": {"credentialRef": "provider:openrouter"}
                    },
                }
            }
        },
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    try:
        inventory = application.mcp.list(project.id)
        server = inventory.servers[0]
        assert server.name == "demo"
        assert server.args == ("server.py", "--token", "••••••")
        assert server.env_keys == ()
        assert server.credential_env_keys == ("API_TOKEN",)
        assert server.header_keys == ()
        assert "secret-value" not in repr(server)
        assert "Bearer secret" not in repr(server)

        with pytest.raises(ProjectNotTrustedError):
            application.mcp.upsert(
                project_id=project.id,
                scope="project",
                name="project-server",
                patch={"type": "stdio", "command": "python3"},
            )

        project = application.projects.update(
            project.id,
            trust_state=TrustState.TRUSTED,
        )
        updated = application.mcp.upsert(
            project_id=project.id,
            scope="project",
            name="project-server",
            patch={
                "type": "streamableHttp",
                "url": "https://mcp.example.test/rpc",
                "enabledTools": ["search"],
            },
        )
        assert {server.name for server in updated.servers} == {
            "demo",
            "project-server",
        }
        project_config = json.loads(
            (workspace / "deepcode_config.json").read_text(encoding="utf-8")
        )
        assert (
            project_config["mcpServers"]["project-server"]["url"]
            == "https://mcp.example.test/rpc"
        )
        assert (
            "project-server"
            not in json.loads(
                (home / "deepcode_config.json").read_text(encoding="utf-8")
            )["mcpServers"]
        )

        inherited = application.mcp.remove(
            project_id=project.id,
            scope="project",
            name="project-server",
        )
        assert [server.name for server in inherited.servers] == ["demo"]
    finally:
        application.close()


def test_mcp_inventory_includes_plugin_servers_with_effective_user_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    plugin = resolve_plugin(_write_mcp_plugin(tmp_path / "review-tools"))
    registration = LocalPluginRegistry(home / "plugins" / "registry.json").add(plugin)
    _write_json(
        home / "deepcode_config.json",
        {
            "pluginMcpServers": {
                "review-tools/context": {
                    "approvalMode": "prompt",
                    "enabledTools": ["inspect_repository"],
                }
            }
        },
    )

    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        inventory = application.mcp.list(project.id)

        assert len(inventory.servers) == 1
        server = inventory.servers[0]
        assert server.id == "review-tools--context"
        assert server.name == "review-tools/context"
        assert server.plugin_id == registration.installation_id
        assert server.policy_key == "review-tools/context"
        assert server.source == "plugin"
        assert server.transport == "streamableHttp"
        assert server.approval_mode == "prompt"
        assert server.enabled_tools == ("inspect_repository",)
        assert server.configuration_state == "configured"
    finally:
        application.close()


def test_mcp_presets_add_disabled_and_real_probe_reports_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    fixture = Path(__file__).parents[1] / "fixtures" / "mcp_runtime_server.py"
    _write_json(
        home / "deepcode_config.json",
        {
            "mcpServers": {
                "fixture": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(fixture)],
                    "enabled": False,
                }
            }
        },
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        presets = application.mcp.list_presets(project.id)
        assert len(presets.presets) == 16
        notion = next(item for item in presets.presets if item.id == "notion")
        assert notion.auth == "oauth"
        assert notion.configured is False

        configured = application.mcp.add_preset("context7", project_id=project.id)
        context7 = next(item for item in configured.servers if item.name == "context7")
        assert context7.configuration_state == "disabled"
        stored = json.loads((home / "deepcode_config.json").read_text())
        assert stored["mcpServers"]["context7"]["enabled"] is False
        with pytest.raises(InvalidArgumentError, match="already configured"):
            application.mcp.add_preset("context7", project_id=project.id)

        result = application.mcp.probe("fixture", project_id=project.id)
        assert result.ok is True
        assert result.tool_count == 3
        assert result.resource_count == 1
        assert result.prompt_count == 1
        fixture_info = next(
            item
            for item in application.mcp.list(project.id).servers
            if item.name == "fixture"
        )
        assert fixture_info.runtime_state == "tested"
        assert fixture_info.runtime_message == (
            "Connection test passed; the one-shot connection is closed"
        )
        assert fixture_info.tool_count == 3
    finally:
        application.close()


def test_settings_layering_and_scoped_updates_do_not_copy_project_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    _write_json(
        home / "deepcode_config.json",
        {
            "agents": {"defaults": {"model": "openai/user"}},
            "providers": {"openai": {"apiKey": "user-secret"}},
        },
    )
    _write_json(
        workspace / "deepcode_config.json",
        {"agents": {"defaults": {"model": "openai/project"}}},
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    try:
        effective = application.settings.read(project.id)
        assert effective["agents"]["defaults"]["model"] == "openai/project"
        assert (
            next(
                provider
                for provider in effective["providers"]
                if provider["name"] == "openai"
            )["configured"]
            is True
        )

        with pytest.raises(ProjectNotTrustedError):
            application.settings.update(
                {"security": {"sandbox": False}},
                scope="project",
                project_id=project.id,
            )

        application.projects.update(project.id, trust_state=TrustState.TRUSTED)
        monkeypatch.chdir(workspace)
        project_result = application.settings.update(
            {"security": {"sandbox": False}},
            scope="project",
            project_id=project.id,
        )
        assert project_result["security"]["sandbox"] is False
        project_raw = json.loads(
            (workspace / "deepcode_config.json").read_text(encoding="utf-8")
        )
        assert project_raw == {
            "agents": {"defaults": {"model": "openai/project"}},
            "security": {"sandbox": False},
        }

        user_result = application.settings.update(
            {"security": {"permissionMode": "plan"}},
            scope="user",
            project_id=project.id,
        )
        assert user_result["agents"]["defaults"]["model"] == "openai/project"
        assert user_result["security"] == {
            "accessPreset": None,
            "permissionMode": "plan",
            "permissions": {},
            "sandbox": False,
        }
        user_raw = json.loads(
            (home / "deepcode_config.json").read_text(encoding="utf-8")
        )
        assert user_raw["agents"]["defaults"]["model"] == "openai/user"
        assert "project" not in json.dumps(user_raw)
        assert user_raw["security"] == {"permissionMode": "plan"}
    finally:
        application.close()


def test_settings_projects_resolved_access_and_unsets_scope_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    _write_json(
        home / "deepcode_config.json",
        {"security": {"accessPreset": "ask"}},
    )
    _write_json(
        workspace / "deepcode_config.json",
        {"security": {"accessPreset": "read_only"}},
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    try:
        effective = application.settings.read(project.id)
        assert effective["userAccessPreset"] == "ask"
        assert effective["projectAccessPreset"] == "read_only"
        assert effective["resolvedDefaultSecuritySource"] == "project"
        assert (
            effective["resolvedDefaultSecurityProfile"]["accessPreset"] == "read_only"
        )

        inherited = application.settings.update(
            {"security": {"accessPreset": None}},
            scope="project",
            project_id=project.id,
        )

        assert inherited["projectAccessPreset"] is None
        assert inherited["resolvedDefaultSecuritySource"] == "user"
        assert inherited["resolvedDefaultSecurityProfile"]["accessPreset"] == "ask"
        project_raw = json.loads(
            (workspace / "deepcode_config.json").read_text(encoding="utf-8")
        )
        assert "accessPreset" not in project_raw.get("security", {})
    finally:
        application.close()


def test_settings_projects_environment_legacy_security_without_ui_guessing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.setenv("DEEPCODE_PERMISSION_MODE", "full_auto")
    monkeypatch.setenv("DEEPCODE_SANDBOX", "false")
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    try:
        settings = application.settings.read()
        resolved = settings["resolvedDefaultSecurityProfile"]
        assert settings["resolvedDefaultSecuritySource"] == "environment"
        assert resolved["accessPreset"] is None
        assert resolved["permissionMode"] == "full_auto"
        assert resolved["commandSandbox"] is False
        assert resolved["filesystemScope"] == "workspace"
    finally:
        application.close()


def test_settings_read_keeps_history_available_when_project_directory_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    _write_json(
        home / "deepcode_config.json",
        {
            "agents": {"defaults": {"model": "openai/user-default"}},
            "providers": {"openai": {"apiKey": "user-secret"}},
        },
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(
        str(workspace),
        trust_state=TrustState.TRUSTED,
    )
    workspace.rmdir()

    try:
        effective = application.settings.read(project.id)

        assert effective["agents"]["defaults"]["model"] == "openai/user-default"
        assert effective["configPath"] == str(home / "deepcode_config.json")
        assert (
            next(
                provider
                for provider in effective["providers"]
                if provider["name"] == "openai"
            )["configured"]
            is True
        )

        with pytest.raises(
            InvalidArgumentError,
            match="project path does not exist",
        ):
            application.settings.update(
                {"security": {"sandbox": False}},
                scope="project",
                project_id=project.id,
            )
    finally:
        application.close()


def test_diagnostics_reports_local_health_without_provider_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("DEEPCODE_HOME", str(home))
    monkeypatch.chdir(workspace)
    _write_json(
        home / "deepcode_config.json",
        {"providers": {"openai": {"apiKey": "never-expose-this"}}},
    )
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    project = application.projects.add(str(workspace))
    application.threads.start(project.id, title="Diagnostics")
    try:
        snapshot = application.diagnostics.read(project.id)
        serialized = json.dumps(snapshot)
        assert snapshot["projectPath"] == str(workspace)
        assert snapshot["sessionCount"] == 1
        assert snapshot["threadCount"] == 1
        assert snapshot["automationCount"] == 0
        assert snapshot["databaseSchemaVersion"] == LATEST_SCHEMA_VERSION
        assert any(check["id"] == "database" for check in snapshot["checks"])
        assert "never-expose-this" not in serialized
    finally:
        application.close()
