from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.application import DeepCodeApplication
from core.application.errors import InvalidArgumentError, ProjectNotTrustedError
from core.domain import TrustState
from core.persistence.migrations import LATEST_SCHEMA_VERSION
from core.skills.management import LocalSkillManager
from core.skills.runtime import SkillRuntime


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


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
            "tools": {
                "mcpServers": {
                    "demo": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["server.py", "--token", "secret-value"],
                        "env": {"API_TOKEN": "secret"},
                        "headers": {"Authorization": "Bearer secret"},
                    }
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
        assert server.env_keys == ("API_TOKEN",)
        assert server.header_keys == ("Authorization",)
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
            project_config["tools"]["mcpServers"]["project-server"]["url"]
            == "https://mcp.example.test/rpc"
        )
        assert (
            "project-server"
            not in json.loads(
                (home / "deepcode_config.json").read_text(encoding="utf-8")
            )["tools"]["mcpServers"]
        )

        inherited = application.mcp.remove(
            project_id=project.id,
            scope="project",
            name="project-server",
        )
        assert [server.name for server in inherited.servers] == ["demo"]
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
