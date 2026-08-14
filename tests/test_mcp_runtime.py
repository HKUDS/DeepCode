from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent_runtime.tools.registry import ToolRegistry
from core.domain.execution_security import ApprovalPolicy
from core.harness.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
)
from core.mcp import (
    McpConfigResolver,
    McpConfigurationError,
    McpRuntimePlan,
    McpServerDefinition,
    McpServerSource,
    McpSessionRuntime,
    McpStartupError,
    ResolvedMcpServer,
)
from core.mcp.tools import McpToolAdapter

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"


def _server(
    workspace: Path,
    **overrides,
) -> ResolvedMcpServer:
    raw = {
        "type": "stdio",
        "command": sys.executable,
        "args": [str(FIXTURE_SERVER)],
        "env": {"FIXTURE_VALUE": "from-config"},
        "toolTimeoutSeconds": 2,
        **overrides,
    }
    return ResolvedMcpServer(
        server_id="fixture",
        name="fixture",
        source=McpServerSource.USER,
        definition=McpServerDefinition.model_validate(raw),
        config_dir=workspace,
        workspace=workspace,
    )


def test_definition_enforces_transport_boundaries() -> None:
    with pytest.raises(ValidationError, match="cannot define HTTP fields"):
        McpServerDefinition.model_validate(
            {
                "type": "stdio",
                "command": "python",
                "url": "https://example.test/mcp",
            }
        )


def test_resolver_rejects_literal_secrets_in_native_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    user = home / "deepcode_config.json"
    user.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unsafe": {
                        "type": "stdio",
                        "command": "python",
                        "env": {"OPENROUTER_API_KEY": "secret"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigurationError, match="credentialEnv"):
        McpConfigResolver(user).resolve(workspace, project_trusted=True)


def test_resolver_uses_only_new_top_level_and_requires_project_trust(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    user = home / "deepcode_config.json"
    user.write_text(
        json.dumps(
            {
                "tools": {
                    "mcpServers": {"legacy": {"type": "stdio", "command": "never-run"}}
                },
                "mcpServers": {"shared": {"type": "stdio", "command": "user-command"}},
            }
        ),
        encoding="utf-8",
    )
    (workspace / "deepcode_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"type": "stdio", "command": "project-command"},
                    "project": {"type": "stdio", "command": "project-only"},
                }
            }
        ),
        encoding="utf-8",
    )
    resolver = McpConfigResolver(user)

    blocked = resolver.resolve(workspace, project_trusted=False)
    assert [server.name for server in blocked.servers] == ["shared"]
    assert blocked.servers[0].definition.command == "user-command"
    assert "blocked" in blocked.diagnostics[0].casefold()

    trusted = resolver.resolve(workspace, project_trusted=True)
    assert {server.name for server in trusted.servers} == {"shared", "project"}
    shared = next(server for server in trusted.servers if server.name == "shared")
    assert shared.source is McpServerSource.PROJECT
    assert shared.definition.command == "project-command"


def test_project_layer_cannot_request_user_credentials(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (workspace / "deepcode_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unsafe": {
                        "type": "stdio",
                        "command": "python",
                        "credentialEnv": {
                            "OPENROUTER_API_KEY": {
                                "credentialRef": "provider:openrouter"
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(McpConfigurationError, match="cannot request"):
        McpConfigResolver(home / "deepcode_config.json").resolve(
            workspace,
            project_trusted=True,
        )


def test_user_policy_can_narrow_and_bind_plugin_without_replacing_transport(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    user_config = home / "deepcode_config.json"
    user_config.write_text(
        json.dumps(
            {
                "pluginMcpServers": {
                    "review-tools/runtime": {
                        "enabledTools": ["read_value"],
                        "approvalMode": "prompt",
                        "credentialEnv": {
                            "OPENROUTER_API_KEY": {
                                "credentialRef": "provider:openrouter"
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    plugin = ResolvedMcpServer(
        server_id="plugin.review.runtime.1234567890",
        name="review-tools/runtime",
        source=McpServerSource.PLUGIN,
        definition=McpServerDefinition.model_validate(
            {"type": "stdio", "command": "python3", "approvalMode": "writes"}
        ),
        config_dir=workspace,
        workspace=workspace,
        plugin_id="plg_000000000000000000000000",
        plugin_root=workspace,
        plugin_data=home / "plugins" / "data",
        policy_key="review-tools/runtime",
    )

    resolved = (
        McpConfigResolver(user_config)
        .resolve(
            workspace,
            project_trusted=True,
            plugin_servers=(plugin,),
        )
        .servers[0]
    )

    assert resolved.definition.command == "python3"
    assert resolved.definition.enabled_tools == ("read_value",)
    assert resolved.definition.approval_mode.value == "prompt"
    assert (
        resolved.definition.credential_env["OPENROUTER_API_KEY"].connection_id
        == "openrouter"
    )

    user_config.write_text(
        json.dumps(
            {
                "pluginMcpServers": {
                    "review-tools/runtime": {"command": "replaced-command"}
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(McpConfigurationError, match="command"):
        McpConfigResolver(user_config).resolve(
            workspace,
            project_trusted=True,
            plugin_servers=(plugin,),
        )


@pytest.mark.asyncio
async def test_runtime_discovers_calls_and_closes_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    plan = McpRuntimePlan(tmp_path, (_server(tmp_path),), "test")
    runtime = McpSessionRuntime(plan, registry)
    await runtime.ensure_started()
    try:
        assert runtime.available_server_ids == ("fixture",)
        assert runtime.statuses[0].state == "ready"
        assert runtime.statuses[0].tool_count == 3
        read = registry.get("mcp__fixture__read_value")
        write = registry.get("mcp__fixture__write_value")
        assert isinstance(read, McpToolAdapter)
        assert isinstance(write, McpToolAdapter)
        assert read.identity.raw_name == "read_value"
        assert read.read_only is True
        assert write.read_only is False
        assert str(await read.execute()) == "from-config"
        assert "fixture reads" in (runtime.instruction_context() or "")
    finally:
        await runtime.aclose()
        await registry.aclose()
    assert not registry.tool_names


@pytest.mark.asyncio
async def test_runtime_does_not_forward_ambient_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "ambient-secret")
    registry = ToolRegistry()
    runtime = McpSessionRuntime(
        McpRuntimePlan(tmp_path, (_server(tmp_path),), "test"),
        registry,
    )
    await runtime.ensure_started()
    try:
        read = registry.get("mcp__fixture__read_value")
        assert isinstance(read, McpToolAdapter)
        assert str(await read.execute(name="OPENROUTER_API_KEY")) == "<missing>"
    finally:
        await runtime.aclose()
        await registry.aclose()


@pytest.mark.asyncio
async def test_runtime_resolves_explicit_provider_credential(tmp_path: Path) -> None:
    server = _server(
        tmp_path,
        env={},
        credentialEnv={"OPENROUTER_API_KEY": {"credentialRef": "provider:openrouter"}},
    )
    registry = ToolRegistry()
    runtime = McpSessionRuntime(
        McpRuntimePlan(tmp_path, (server,), "test"),
        registry,
        credential_resolver=lambda connection_id: (
            "stored-secret" if connection_id == "openrouter" else None
        ),
    )
    await runtime.ensure_started()
    try:
        read = registry.get("mcp__fixture__read_value")
        assert isinstance(read, McpToolAdapter)
        assert str(await read.execute(name="OPENROUTER_API_KEY")) == "stored-secret"
    finally:
        await runtime.aclose()
        await registry.aclose()


@pytest.mark.asyncio
async def test_tool_timeout_and_required_startup_failure(tmp_path: Path) -> None:
    registry = ToolRegistry()
    runtime = McpSessionRuntime(
        McpRuntimePlan(
            tmp_path,
            (_server(tmp_path, toolTimeoutSeconds=0.1),),
            "test",
        ),
        registry,
    )
    await runtime.ensure_started()
    try:
        slow = registry.get("mcp__fixture__slow")
        assert isinstance(slow, McpToolAdapter)
        with pytest.raises(TimeoutError, match="timed out"):
            await slow.execute(seconds=2)
    finally:
        await runtime.aclose()
        await registry.aclose()

    broken = ResolvedMcpServer(
        server_id="required",
        name="required",
        source=McpServerSource.USER,
        definition=McpServerDefinition.model_validate(
            {
                "type": "stdio",
                "command": str(tmp_path / "missing-command"),
                "required": True,
                "startupTimeoutSeconds": 1,
            }
        ),
        config_dir=tmp_path,
        workspace=tmp_path,
    )
    registry = ToolRegistry()
    required = McpSessionRuntime(
        McpRuntimePlan(tmp_path, (broken,), "test"),
        registry,
    )
    with pytest.raises(McpStartupError, match="Required MCP"):
        await required.ensure_started()
    await required.aclose()
    await registry.aclose()


def test_mcp_approval_never_widens_global_policy(tmp_path: Path) -> None:
    server = _server(
        tmp_path,
        approvalMode="writes",
        tools={"read_value": {"approvalMode": "auto"}},
    )
    engine = PermissionEngine(mode=PermissionMode.FULL_AUTO)
    assert (
        engine.evaluate_tool(
            "mcp__fixture__read_value",
            {},
            read_only=True,
            approval_mode=server.definition.policy_for("read_value"),
        )[0]
        is PermissionDecision.ALLOW
    )
    assert (
        engine.evaluate_tool(
            "mcp__fixture__write_value",
            {},
            read_only=False,
            approval_mode=server.definition.policy_for("write_value"),
        )[0]
        is PermissionDecision.ASK
    )

    read_only_engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        enforce_read_only=True,
    )
    assert (
        read_only_engine.evaluate_tool(
            "mcp__fixture__write_value",
            {},
            read_only=False,
            approval_mode="approve",
        )[0]
        is PermissionDecision.DENY
    )

    full_access_engine = PermissionEngine(
        mode=PermissionMode.FULL_AUTO,
        approval_policy=ApprovalPolicy.NEVER,
        bypass_origin_approval=True,
    )
    assert (
        full_access_engine.evaluate_tool(
            "mcp__fixture__write_value",
            {},
            read_only=False,
            approval_mode="writes",
        )[0]
        is PermissionDecision.ALLOW
    )
    full_access_engine.rules.append(
        PermissionRule(
            "mcp__fixture__write_value",
            "*",
            PermissionDecision.DENY,
        )
    )
    assert (
        full_access_engine.evaluate_tool(
            "mcp__fixture__write_value",
            {},
            read_only=False,
            approval_mode="writes",
        )[0]
        is PermissionDecision.DENY
    )
