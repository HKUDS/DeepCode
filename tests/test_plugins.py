from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.agent_runtime.tools.registry import ToolRegistry
from core.application.errors import ConflictError, PluginNotFoundError
from core.application.plugin_service import PluginService
from core.mcp import McpConfigResolver, McpSessionRuntime
from core.mcp.tools import McpToolAdapter
from core.plugins.domain import (
    PluginComponentKind,
    PluginComponentStatus,
    PluginValidationError,
)
from core.plugins.formats.agent_plugins_v1 import AGENT_PLUGIN_SCHEMA
from core.plugins.host import LocalPluginHost
from core.plugins.mcp import (
    MCP_SCHEMA,
    load_agent_plugin_mcp,
    plugin_policy_key,
    plugin_server_id,
)
from core.plugins.registry import LocalPluginRegistry
from core.plugins.resolver import resolve_plugin
from core.skills.host import SkillCatalogHost, SkillWorkspaceRegistry
from core.skills.models import (
    LOCAL_SKILL_AUTHORITY,
    SkillResolutionError,
    SkillSelection,
    SkillStatus,
)


def _write_skill(
    root: Path,
    name: str,
    body: str = "Follow the Plugin workflow.",
    *,
    declared_name: str | None = None,
    extra_frontmatter: str = "",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {declared_name or name}\n"
        f"description: Plugin Skill {name}\n{extra_frontmatter}---\n{body}\n",
        encoding="utf-8",
    )
    return directory


def _write_plugin(
    root: Path,
    *,
    name: str = "review-tools",
    version: str = "1.2.3",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write_skill(root / "skills", "plugin-review")
    _write_skill(root / "skills", "plugin-verify")
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": AGENT_PLUGIN_SCHEMA,
                "name": name,
                "version": version,
                "description": "Review and verify code changes.",
            }
        ),
        encoding="utf-8",
    )
    return root


def _skill_hosts() -> SkillWorkspaceRegistry:
    return SkillWorkspaceRegistry(
        lambda path: SkillCatalogHost(
            path,
            include_user=False,
            include_system=False,
        )
    )


def _write_mcp(root: Path, servers: dict) -> None:
    (root / "mcp.json").write_text(
        json.dumps({"$schema": MCP_SCHEMA, "mcpServers": servers}),
        encoding="utf-8",
    )


def test_resolver_normalizes_agent_plugins_v1_package(tmp_path: Path) -> None:
    plugin = resolve_plugin(_write_plugin(tmp_path / "plugin"))

    assert plugin.name == "review-tools"
    assert plugin.package.schema == AGENT_PLUGIN_SCHEMA
    assert plugin.package.metadata.version == "1.2.3"
    assert plugin.package.manifest_revision.startswith("sha256:")
    skills = plugin.package.component(PluginComponentKind.SKILLS)
    assert skills is not None
    assert skills.status is PluginComponentStatus.READY
    assert skills.resource is not None and skills.resource.value == "skills"
    assert skills.item_count == 2


def test_resolver_has_no_legacy_manifest_fallback(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "manifestVersion": 1,
                "name": "legacy-plugin",
                "skills": ["skills"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PluginValidationError, match="supported Agent Plugins"):
        resolve_plugin(root)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda raw: raw.update(name="Not A Slug"), "lowercase"),
        (lambda raw: raw.update(version=1), "version must be a string"),
        (lambda raw: raw.update(author="person"), "author must be an object"),
        (lambda raw: raw.update(keywords=["valid", 2]), "string array"),
    ],
)
def test_manifest_rejects_invalid_standard_fields(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    path = root / "plugin.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutation(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PluginValidationError, match=message):
        resolve_plugin(root)


def test_unknown_fields_and_extension_payloads_are_opaque_warnings(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    path = root / "plugin.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["futureField"] = {"enabled": True}
    raw["extensions"] = {"vendor.example/feature": object().__class__.__name__}
    path.write_text(json.dumps(raw), encoding="utf-8")

    plugin = resolve_plugin(root)

    assert plugin.package.metadata.extension_namespaces == ("vendor.example/feature",)
    assert any(
        diagnostic.code == "agent_plugins.unknown_manifest_field"
        and "futureField" in diagnostic.message
        for diagnostic in plugin.package.diagnostics
    )


def test_fixed_components_are_discovered_and_fail_independently(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    _write_mcp(root, {})
    plugin = resolve_plugin(root)
    mcp = plugin.package.component(PluginComponentKind.MCP)
    assert mcp is not None and mcp.status is PluginComponentStatus.READY
    assert mcp.item_count == 0

    outside = tmp_path / "outside"
    outside.mkdir()
    for child in (root / "skills").iterdir():
        for file in child.iterdir():
            file.unlink()
        child.rmdir()
    (root / "skills").rmdir()
    os.symlink(outside, root / "skills", target_is_directory=True)

    plugin = resolve_plugin(root)
    skills = plugin.package.component(PluginComponentKind.SKILLS)
    assert skills is not None and skills.status is PluginComponentStatus.INVALID
    assert skills.diagnostics[0].component is PluginComponentKind.SKILLS


def test_mcp_component_skips_invalid_servers_without_disabling_plugin(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    _write_mcp(
        root,
        {
            "local": {
                "type": "streamable-http",
                "url": "http://127.0.0.1:8800/mcp",
            },
            "insecure-remote": {
                "type": "streamable-http",
                "url": "http://example.com/mcp",
            },
        },
    )

    plugin = resolve_plugin(root)
    component = plugin.package.component(PluginComponentKind.MCP)

    assert component is not None
    assert component.status is PluginComponentStatus.READY
    assert component.item_count == 1
    assert component.diagnostics[0].code == "agent_plugins.invalid_mcp_server"
    assert "insecure-remote" in component.diagnostics[0].message


def test_mcp_component_accepts_unrestricted_json_server_names(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    _write_mcp(
        root,
        {
            "": {"type": "stdio", "command": "python3"},
            "数据库/α": {
                "type": "stdio",
                "command": "python3",
                "env": {"TOKENIZERS_PARALLELISM": "false"},
            },
            "runtime": {"type": "stdio", "command": "python3"},
        },
    )

    parsed = load_agent_plugin_mcp(root.resolve(), plugin_schema=AGENT_PLUGIN_SCHEMA)

    assert parsed.fatal is False
    assert {server.name for server in parsed.servers} == {"", "数据库/α", "runtime"}
    ids = {plugin_server_id("review-tools", server.name) for server in parsed.servers}
    assert len(ids) == 3
    assert all(1 <= len(server_id) <= 80 for server_id in ids)
    assert plugin_server_id("review-tools", "runtime") == "review-tools--runtime"
    assert plugin_policy_key("review-tools", "数据库/α") == (
        "review-tools/%E6%95%B0%E6%8D%AE%E5%BA%93%2F%CE%B1"
    )


def test_mcp_duplicate_inside_one_server_keeps_other_servers(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    (root / "mcp.json").write_text(
        "{"
        f'"$schema":{json.dumps(MCP_SCHEMA)},'
        '"mcpServers":{'
        '"broken":{"type":"stdio","command":"python3",'
        '"env":{"MODE":"one","MODE":"two"}},'
        '"working":{"type":"stdio","command":"python3"}'
        "}}",
        encoding="utf-8",
    )

    plugin = resolve_plugin(root)
    component = plugin.package.component(PluginComponentKind.MCP)

    assert component is not None
    assert component.status is PluginComponentStatus.READY
    assert component.item_count == 1
    assert "broken" in component.diagnostics[0].message
    assert "Duplicate key in stdio env" in component.diagnostics[0].message


@pytest.mark.asyncio
async def test_plugin_mcp_runs_through_generic_runtime_with_private_data(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = _write_plugin(tmp_path / "plugin")
    metadata = root / "skills" / "plugin-review" / "agents"
    metadata.mkdir()
    (metadata / "openai.yaml").write_text(
        "dependencies:\n  tools:\n    - type: mcp\n      value: runtime\n",
        encoding="utf-8",
    )
    fixture = Path(__file__).parent / "fixtures" / "mcp_runtime_server.py"
    (root / "server.py").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_mcp(
        root,
        {
            "runtime": {
                "type": "stdio",
                "command": "python3",
                "args": ["${PLUGIN_ROOT}/server.py"],
            }
        },
    )
    registry_path = tmp_path / "home" / "plugins" / "registry.json"
    plugin_registry = LocalPluginRegistry(registry_path)
    registration = plugin_registry.add(resolve_plugin(root))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(
        skill_hosts,
        registry=plugin_registry,
        monitor=False,
    )
    tools = ToolRegistry()
    runtime = None
    try:
        contributions = plugin_host.mcp_servers(workspace)
        assert len(contributions) == 1
        server = contributions[0]
        assert server.plugin_id == registration.installation_id
        assert server.plugin_root == root.resolve()
        assert server.plugin_data is not None and server.plugin_data.is_dir()
        assert server.policy_key == "review-tools/runtime"

        plan = McpConfigResolver(tmp_path / "home" / "deepcode_config.json").resolve(
            workspace,
            project_trusted=True,
            plugin_servers=contributions,
        )
        runtime = McpSessionRuntime(plan, tools)
        await runtime.ensure_started()
        read = next(
            tool
            for name in tools.tool_names
            if isinstance((tool := tools.get(name)), McpToolAdapter)
            and tool.identity.raw_name == "read_value"
        )
        assert str(await read.execute(name="PLUGIN_ROOT")) == str(root.resolve())
        assert str(await read.execute(name="PLUGIN_DATA")) == str(server.plugin_data)

        skill_runtime = skill_hosts.new_runtime(workspace)
        review = skill_runtime.catalog().get_active("plugin-review")
        assert review is not None
        context, token = skill_runtime.begin_turn(
            "review",
            (SkillSelection(review.id),),
            available_tools=tuple(tools.tool_names),
            available_mcp_servers=runtime.skill_capabilities,
        )
        try:
            assert context.snapshot.records[0].name == "plugin-review"
        finally:
            skill_runtime.end_turn(token)
    finally:
        if runtime is not None:
            await runtime.aclose()
        await tools.aclose()
        plugin_host.close()
        skill_hosts.close()


def test_manifest_rejects_unknown_schema_duplicate_keys_and_symlink_manifest(
    tmp_path: Path,
) -> None:
    root = _write_plugin(tmp_path / "plugin")
    (root / "plugin.json").write_text(
        '{"$schema":"x","name":"one","name":"two"}',
        encoding="utf-8",
    )
    with pytest.raises(PluginValidationError, match="Duplicate key"):
        resolve_plugin(root)

    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"$schema": AGENT_PLUGIN_SCHEMA, "name": "outside"}),
        encoding="utf-8",
    )
    (root / "plugin.json").unlink()
    os.symlink(outside, root / "plugin.json")
    with pytest.raises(PluginValidationError, match="outside"):
        resolve_plugin(root)


def test_plugin_skills_are_direct_children_and_strictly_validated(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = _write_plugin(tmp_path / "plugin")
    (root / "skills" / "SKILL.md").write_text(
        "---\nname: root-skill\ndescription: Must not load\n---\nIgnore.\n",
        encoding="utf-8",
    )
    _write_skill(
        root / "skills",
        "wrong-directory",
        declared_name="different-name",
    )
    _write_skill(
        root / "skills",
        "tool-skill",
        extra_frontmatter="allowed-tools: Read Write\n",
    )
    registration = LocalPluginRegistry(tmp_path / "registry.json")
    registration.add(resolve_plugin(root))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(
        skill_hosts,
        registry=registration,
        monitor=False,
    )
    try:
        catalog = skill_hosts.new_runtime(workspace).catalog()
        assert catalog.get_active("root-skill") is None
        assert catalog.get_active("different-name") is None
        assert catalog.get_active("tool-skill").allowed_tools == ("Read", "Write")
        assert any("wrong-directory" in warning for warning in catalog.warnings)
    finally:
        plugin_host.close()
        skill_hosts.close()


def test_standalone_skill_keeps_deepcode_compat_profile(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = workspace / ".agents" / "skills" / "directory-name"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: Compatible_Name\ndescription: local\n"
        "allowed-tools: Read,Write\n---\nLocal instructions.\n",
        encoding="utf-8",
    )

    catalog = SkillCatalogHost(
        workspace,
        include_user=False,
        include_system=False,
    ).catalog()
    record = catalog.get_active("Compatible_Name")
    assert record is not None and record.allowed_tools == ("Read", "Write")


def test_registry_uses_stable_installation_identity_without_copying_source(
    tmp_path: Path,
) -> None:
    plugin = resolve_plugin(_write_plugin(tmp_path / "plugin"))
    registry = LocalPluginRegistry(tmp_path / "home" / "plugins" / "registry.json")

    registration = registry.add(plugin)
    assert registration.installation_id.startswith("plg_")
    assert registration.name == plugin.name
    assert registration.source.path == plugin.root
    registry.set_enabled(registration.installation_id, False)
    assert registry.list()[0].enabled is False
    removed = registry.remove(registration.installation_id)

    assert removed.source.path == plugin.root
    assert plugin.manifest_path.is_file()
    assert registry.list() == ()


def test_plugin_service_maps_registry_outcomes_without_parsing_messages(
    tmp_path: Path,
) -> None:
    plugin_root = _write_plugin(tmp_path / "plugin")
    skill_hosts = SkillWorkspaceRegistry()
    plugin_host = LocalPluginHost(
        skill_hosts,
        registry=LocalPluginRegistry(tmp_path / "registry.json"),
        monitor=False,
    )
    service = PluginService(plugin_host)
    try:
        discovery = service.add(str(plugin_root))
        plugin_id = discovery.plugins[0].id
        assert discovery.plugins[0].schema == AGENT_PLUGIN_SCHEMA
        with pytest.raises(ConflictError):
            service.add(str(plugin_root))
        with pytest.raises(PluginNotFoundError):
            service.set_enabled("missing-plugin", enabled=False)
        with pytest.raises(PluginNotFoundError):
            service.remove("missing-plugin")
        assert service.remove(plugin_id).id == plugin_id
    finally:
        service.close()
        skill_hosts.close()


def test_plugin_host_contributes_skills_through_installation_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = LocalPluginRegistry(tmp_path / "registry.json")
    registration = registry.add(resolve_plugin(_write_plugin(tmp_path / "plugin")))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(skill_hosts, registry=registry, monitor=False)
    runtime = skill_hosts.new_runtime(workspace)
    try:
        review = runtime.catalog().get_active("plugin-review")
        assert review is not None
        assert review.source == f"custom:{registration.installation_id}"
        assert runtime.read(review.id).require_instructions() == (
            "Follow the Plugin workflow."
        )
        matches = runtime.search_package_resources(review, "Plugin workflow")
        assert matches.matches[0].reference.package == review.package_id
    finally:
        plugin_host.close()
        skill_hosts.close()


def test_standalone_and_plugin_skills_share_catalog_with_local_precedence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(
        workspace / ".agents" / "skills",
        "plugin-review",
        body="Use the standalone project workflow.",
    )
    registry = LocalPluginRegistry(tmp_path / "registry.json")
    registration = registry.add(resolve_plugin(_write_plugin(tmp_path / "plugin")))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(skill_hosts, registry=registry, monitor=False)
    runtime = skill_hosts.new_runtime(workspace)
    try:
        catalog = runtime.catalog()
        review = catalog.get_active("plugin-review")
        verify = catalog.get_active("plugin-verify")
        assert review is not None and review.authority == LOCAL_SKILL_AUTHORITY
        assert verify is not None
        assert verify.source == f"custom:{registration.installation_id}"
        plugin_review = next(
            record
            for record in catalog.records
            if record.name == "plugin-review"
            and record.authority != LOCAL_SKILL_AUTHORITY
        )
        assert plugin_review.status is SkillStatus.SHADOWED
        assert plugin_review.shadowed_by == review.id
    finally:
        plugin_host.close()
        skill_hosts.close()


def test_disable_changes_next_turn_without_mutating_active_turn(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = LocalPluginRegistry(tmp_path / "registry.json")
    registration = registry.add(resolve_plugin(_write_plugin(tmp_path / "plugin")))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(skill_hosts, registry=registry, monitor=False)
    service = PluginService(plugin_host)
    runtime = skill_hosts.new_runtime(workspace)
    context, token = runtime.begin_turn("$plugin-review")
    try:
        service.set_enabled(registration.installation_id, enabled=False)
        assert context.snapshot.records[0].require_instructions() == (
            "Follow the Plugin workflow."
        )
        implicit, added = runtime.load_implicit("plugin-verify")
        assert added is True
        assert implicit.require_instructions() == "Follow the Plugin workflow."
    finally:
        runtime.end_turn(token)
    try:
        assert runtime.catalog(force=True).get_active("plugin-review") is None
        with pytest.raises(SkillResolutionError, match="not found"):
            runtime.read(context.snapshot.records[0].id)
    finally:
        service.close()
        skill_hosts.close()


def test_invalid_manifest_is_isolated_from_local_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_skill(workspace / ".agents" / "skills", "local-review")
    root = _write_plugin(tmp_path / "plugin")
    registry = LocalPluginRegistry(tmp_path / "registry.json")
    registry.add(resolve_plugin(root))
    (root / "plugin.json").write_text("{", encoding="utf-8")
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(skill_hosts, registry=registry, monitor=False)
    try:
        result = plugin_host.snapshot().plugins[0]
        assert result.status.value == "invalid"
        assert result.error
        assert (
            skill_hosts.new_runtime(workspace).catalog().get_active("local-review")
            is not None
        )
    finally:
        plugin_host.close()
        skill_hosts.close()


def test_host_observes_registry_and_fixed_component_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = _write_plugin(tmp_path / "plugin")
    registry_path = tmp_path / "registry.json"
    registry = LocalPluginRegistry(registry_path)
    registration = registry.add(resolve_plugin(root))
    skill_hosts = _skill_hosts()
    plugin_host = LocalPluginHost(skill_hosts, registry=registry, monitor=False)
    runtime = skill_hosts.new_runtime(workspace)
    try:
        assert runtime.catalog().get_active("plugin-review") is not None
        LocalPluginRegistry(registry_path).set_enabled(
            registration.installation_id,
            False,
        )
        assert plugin_host.poll_once() is True
        assert runtime.catalog(force=True).get_active("plugin-review") is None

        LocalPluginRegistry(registry_path).set_enabled(
            registration.installation_id,
            True,
        )
        assert plugin_host.poll_once() is True
        assert runtime.catalog(force=True).get_active("plugin-review") is not None

        previous_revision = plugin_host.snapshot().revision
        _write_mcp(
            root,
            {
                "runtime": {
                    "type": "streamable-http",
                    "url": "http://127.0.0.1:8800/mcp",
                }
            },
        )
        assert plugin_host.poll_once() is True
        assert plugin_host.snapshot().revision != previous_revision
        assert plugin_host.mcp_servers(workspace)[0].policy_key == (
            "review-tools/runtime"
        )
    finally:
        plugin_host.close()
        skill_hosts.close()
