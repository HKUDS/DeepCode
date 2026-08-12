"""Resolve independently-authored MCP configuration layers without deep merging."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.mcp.models import (
    McpConfigurationError,
    McpRuntimePlan,
    McpServerDefinition,
    McpServerPolicyOverlay,
    McpServerSource,
    ResolvedMcpServer,
    validate_no_literal_secrets,
    validate_server_name,
)

_CONFIG_FILENAME = "deepcode_config.json"
_MAX_CONFIG_BYTES = 4 * 1024 * 1024


class McpConfigResolver:
    """Build one immutable MCP plan for a workspace.

    A server entry is atomic at each authority boundary. Project entries may
    replace a same-named user entry only after the caller supplies an explicit
    trust decision; executable fields are never deep-merged across layers.
    """

    def __init__(self, user_config_path: str | Path | None = None) -> None:
        if user_config_path is None:
            configured_home = os.environ.get("DEEPCODE_HOME")
            home = (
                Path(configured_home).expanduser()
                if configured_home
                else Path.home() / ".deepcode"
            )
            user_config_path = home / _CONFIG_FILENAME
        self.user_config_path = (
            Path(user_config_path).expanduser().resolve(strict=False)
        )

    def resolve(
        self,
        workspace: str | Path,
        *,
        project_trusted: bool,
        plugin_servers: Iterable[ResolvedMcpServer] = (),
        include_disabled: bool = False,
    ) -> McpRuntimePlan:
        root = Path(workspace).expanduser().resolve(strict=False)
        project_path = _project_config_path(root)
        user_raw = _read_layer(self.user_config_path)
        project_raw = _read_layer(project_path)
        diagnostics: list[str] = []

        effective = self._parse_layer(
            user_raw,
            source=McpServerSource.USER,
            config_dir=self.user_config_path.parent,
            workspace=root,
        )
        if project_raw:
            if project_trusted:
                project = self._parse_layer(
                    project_raw,
                    source=McpServerSource.PROJECT,
                    config_dir=project_path.parent,
                    workspace=root,
                )
                # Whole-entry replacement is intentional: a project cannot
                # silently inherit a user's command, environment, or auth.
                effective.update(project)
            elif _raw_servers(project_raw):
                diagnostics.append(
                    "Project MCP configuration is blocked until the workspace is trusted"
                )

        plugin_overlays = _raw_plugin_policies(user_raw)
        resolved_plugins = resolve_plugin_servers(plugin_servers, user_raw)
        used_plugin_overlays: set[str] = set()
        for contribution in resolved_plugins:
            policy_key = contribution.policy_key
            if policy_key is not None and policy_key in plugin_overlays:
                used_plugin_overlays.add(policy_key)
            if contribution.server_id in effective:
                raise McpConfigurationError(
                    f"Duplicate MCP server identity: {contribution.server_id}"
                )
            effective[contribution.server_id] = contribution
        for unused in sorted(plugin_overlays.keys() - used_plugin_overlays):
            diagnostics.append(
                f"Plugin MCP policy {unused!r} does not match an active Plugin server"
            )

        servers = tuple(
            server
            for _identity, server in sorted(effective.items())
            if include_disabled or server.definition.enabled
        )
        revision = _revision(
            root,
            user_raw,
            project_raw if project_trusted else {},
            plugin_overlays,
            servers,
        )
        return McpRuntimePlan(
            workspace=root,
            servers=servers,
            revision=revision,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _parse_layer(
        raw: dict[str, Any],
        *,
        source: McpServerSource,
        config_dir: Path,
        workspace: Path,
    ) -> dict[str, ResolvedMcpServer]:
        result: dict[str, ResolvedMcpServer] = {}
        for raw_name, value in sorted(_raw_servers(raw).items()):
            try:
                name = validate_server_name(raw_name)
                if not isinstance(value, dict):
                    raise McpConfigurationError("server entry must be an object")
                definition = McpServerDefinition.model_validate(value)
                validate_no_literal_secrets(definition)
            except (ValidationError, ValueError, TypeError) as exc:
                raise McpConfigurationError(
                    f"Invalid {source.value} MCP server {raw_name!r}: {exc}"
                ) from exc
            if source is McpServerSource.PROJECT and (
                definition.credential_env or definition.bearer_token_credential
            ):
                raise McpConfigurationError(
                    f"Project MCP server {name!r} cannot request stored provider "
                    "credentials; configure that server at user scope"
                )
            result[name] = ResolvedMcpServer(
                server_id=name,
                name=name,
                source=source,
                definition=definition,
                config_dir=config_dir,
                workspace=workspace,
            )
        return result


def resolve_plugin_servers(
    plugin_servers: Iterable[ResolvedMcpServer],
    user_config: dict[str, Any],
) -> tuple[ResolvedMcpServer, ...]:
    """Apply user-owned policy to Plugin contributions without starting them.

    Both the session planner and management inventory use this function, so a
    Plugin MCP server cannot appear with one policy in the UI and run with a
    different policy in the Agent session.
    """

    overlays = _raw_plugin_policies(user_config)
    resolved: list[ResolvedMcpServer] = []
    for contribution in plugin_servers:
        if contribution.source is not McpServerSource.PLUGIN:
            raise McpConfigurationError(
                "plugin_servers may contain only Plugin MCP contributions"
            )
        policy_key = contribution.policy_key
        if policy_key is not None and policy_key in overlays:
            contribution = _apply_plugin_policy(contribution, overlays[policy_key])
        resolved.append(contribution)
    return tuple(resolved)


def _raw_servers(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("mcpServers", raw.get("mcp_servers", {}))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpConfigurationError("mcpServers must contain an object")
    return dict(value)


def _raw_plugin_policies(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("pluginMcpServers", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpConfigurationError("pluginMcpServers must contain an object")
    return dict(value)


def _apply_plugin_policy(
    server: ResolvedMcpServer,
    raw: Any,
) -> ResolvedMcpServer:
    if not isinstance(raw, dict):
        raise McpConfigurationError(
            f"Plugin MCP policy {server.policy_key!r} must be an object"
        )
    try:
        overlay = McpServerPolicyOverlay.model_validate(raw)
        updates = overlay.model_dump(exclude_unset=True, exclude_none=True)
        definition = McpServerDefinition.model_validate(
            {**server.definition.model_dump(), **updates}
        )
    except ValidationError as exc:
        raise McpConfigurationError(
            f"Invalid Plugin MCP policy {server.policy_key!r}: {exc}"
        ) from exc
    return ResolvedMcpServer(
        server_id=server.server_id,
        name=server.name,
        source=server.source,
        definition=definition,
        config_dir=server.config_dir,
        workspace=server.workspace,
        plugin_id=server.plugin_id,
        plugin_root=server.plugin_root,
        plugin_data=server.plugin_data,
        policy_key=server.policy_key,
        plugin_server_name=server.plugin_server_name,
    )


def _project_config_path(workspace: Path) -> Path:
    for candidate in (workspace, *workspace.parents):
        path = candidate / _CONFIG_FILENAME
        if path.exists():
            return path
    return workspace / _CONFIG_FILENAME


def _read_layer(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            payload = stream.read(_MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise McpConfigurationError(f"Unable to read MCP config {path}: {exc}") from exc
    if len(payload) > _MAX_CONFIG_BYTES:
        raise McpConfigurationError(
            f"MCP config exceeds {_MAX_CONFIG_BYTES} bytes: {path}"
        )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpConfigurationError(f"Invalid MCP config JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise McpConfigurationError(f"MCP config must contain an object: {path}")
    return value


def _revision(
    workspace: Path,
    user_raw: dict[str, Any],
    project_raw: dict[str, Any],
    plugin_overlays: dict[str, Any],
    servers: tuple[ResolvedMcpServer, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(str(workspace).encode())
    for raw in (user_raw, project_raw):
        digest.update(
            json.dumps(
                raw.get("mcpServers", {}), sort_keys=True, separators=(",", ":")
            ).encode()
        )
    digest.update(
        json.dumps(plugin_overlays, sort_keys=True, separators=(",", ":")).encode()
    )
    for server in servers:
        digest.update(server.server_id.encode())
        digest.update(server.source.value.encode())
        digest.update(server.definition.model_dump_json(by_alias=True).encode())
    return f"sha256:{digest.hexdigest()}"


__all__ = ["McpConfigResolver", "resolve_plugin_servers"]
