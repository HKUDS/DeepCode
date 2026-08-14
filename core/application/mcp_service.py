"""Safe MCP inventory and scoped atomic configuration mutations."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger
from pydantic import ValidationError

from core.application.config_store import ConfigStore, deep_merge
from core.application.errors import InvalidArgumentError, ProjectNotTrustedError
from core.application.project_service import ProjectService
from core.config import home_config_path, project_config_path
from core.domain.project import TrustState
from core.mcp.models import (
    McpServerDefinition,
    ResolvedMcpServer,
    validate_no_literal_secrets,
    validate_server_name,
)
from core.mcp.oauth import (
    McpOAuthCredentialStore,
    McpOAuthFlowInfo,
    McpOAuthManager,
    create_mcp_oauth_provider,
)
from core.mcp.presets import McpPreset, McpPresetCatalog
from core.mcp.probe import McpProbeResult, probe_mcp_server
from core.mcp.resolver import McpConfigResolver, resolve_plugin_servers

_SERVER_FIELDS = frozenset(
    {
        "type",
        "command",
        "args",
        "cwd",
        "env",
        "envVars",
        "requiredEnvVars",
        "credentialEnv",
        "url",
        "auth",
        "headers",
        "envHttpHeaders",
        "envUrlParams",
        "bearerTokenEnvVar",
        "bearerTokenCredential",
        "enabled",
        "required",
        "supportsParallelToolCalls",
        "startupTimeoutSeconds",
        "toolTimeoutSeconds",
        "enabledTools",
        "disabledTools",
        "approvalMode",
        "tools",
        "description",
    }
)
_SECRET_FLAG = re.compile(
    r"(?:token|api[-_]?key|password|passwd|secret|authorization|auth|header)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class McpServerInfo:
    id: str
    name: str
    plugin_id: str | None
    policy_key: str | None
    transport: str
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    url: str | None
    auth: str | None
    enabled: bool
    required: bool
    enabled_tools: tuple[str, ...] | None
    disabled_tools: tuple[str, ...]
    startup_timeout_seconds: float
    tool_timeout_seconds: float
    approval_mode: str
    description: str | None
    env_keys: tuple[str, ...]
    forwarded_env_keys: tuple[str, ...]
    required_env_keys: tuple[str, ...]
    missing_env_keys: tuple[str, ...]
    credential_env_keys: tuple[str, ...]
    header_keys: tuple[str, ...]
    source: str
    configuration_state: str
    configuration_message: str
    auth_state: str
    runtime_state: str
    runtime_message: str
    tool_count: int
    resource_count: int
    prompt_count: int


@dataclass(frozen=True, slots=True)
class McpInventory:
    servers: tuple[McpServerInfo, ...]
    user_config_path: str
    project_config_path: str | None


@dataclass(frozen=True, slots=True)
class McpPresetInfo:
    id: str
    display_name: str
    category: str
    description: str
    docs_url: str
    transport: str
    auth: str | None
    requires: str
    note: str
    required_environment: tuple[str, ...]
    missing_environment: tuple[str, ...]
    configured: bool


@dataclass(frozen=True, slots=True)
class McpPresetInventory:
    presets: tuple[McpPresetInfo, ...]
    source: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class _RuntimeSnapshot:
    state: str
    message: str
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0


class McpService:
    """One mutation surface shared by App Server, Desktop, and CLI."""

    def __init__(
        self,
        projects: ProjectService,
        *,
        plugin_servers: Callable[[Path], tuple[ResolvedMcpServer, ...]] | None = None,
        credential_resolver: Callable[[str, str | None], str | None] | None = None,
        preset_catalog: McpPresetCatalog | None = None,
        oauth_manager: McpOAuthManager | None = None,
    ) -> None:
        self.projects = projects
        self._plugin_servers = plugin_servers
        self._credential_resolver = credential_resolver
        self._presets = preset_catalog or McpPresetCatalog()
        self._owns_oauth = oauth_manager is None
        self._oauth = oauth_manager or McpOAuthManager()
        self._runtime: dict[tuple[str, str], _RuntimeSnapshot] = {}
        self._listeners: dict[str, Callable[[], None]] = {}
        self._lock = threading.RLock()
        self._oauth_subscription = self._oauth.subscribe(self._on_oauth_change)

    def close(self) -> None:
        self._oauth.unsubscribe(self._oauth_subscription)
        if self._owns_oauth:
            self._oauth.close()
        with self._lock:
            self._listeners.clear()

    def subscribe_changes(self, listener: Callable[[], None]) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._listeners[token] = listener
        return token

    def unsubscribe_changes(self, token: str) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def list(self, project_id: str | None = None) -> McpInventory:
        project = self.projects.read(project_id) if project_id is not None else None
        workspace = (
            Path(project.canonical_path).resolve(strict=True)
            if project is not None
            else None
        )
        project_path = project_config_path(workspace) if workspace is not None else None
        user_config = ConfigStore(home_config_path()).read()
        user_raw = _raw_servers(user_config)
        project_raw = (
            _raw_servers(ConfigStore(project_path).read())
            if project_path is not None and project_path.is_file()
            else {}
        )

        entries: list[McpServerInfo] = []
        effective = dict(user_raw)
        sources = {name: ("user", home_config_path().parent) for name in user_raw}
        if project is not None and project.trust_state is TrustState.TRUSTED:
            effective.update(project_raw)
            assert project_path is not None
            sources.update(
                {name: ("project", project_path.parent) for name in project_raw}
            )
        for name, raw in sorted(effective.items()):
            source, config_dir = sources[name]
            entries.append(
                _server_info(name, raw, source=source, config_dir=config_dir)
            )
        if project is not None and project.trust_state is not TrustState.TRUSTED:
            assert project_path is not None
            for name, raw in sorted(project_raw.items()):
                info = _server_info(
                    name,
                    raw,
                    source="project",
                    config_dir=project_path.parent,
                )
                entries.append(
                    replace(
                        info,
                        configuration_state="blocked",
                        configuration_message=(
                            "Project MCP configuration is blocked until the "
                            "workspace is trusted"
                        ),
                    )
                )
        if workspace is not None and self._plugin_servers is not None:
            contributions = resolve_plugin_servers(
                self._plugin_servers(workspace),
                user_config,
            )
            entries.extend(
                _resolved_server_info(server)
                for server in sorted(contributions, key=lambda item: item.name)
            )
        runtime_workspace = workspace or Path.cwd().resolve(strict=False)
        entries = [self._decorate_status(entry, runtime_workspace) for entry in entries]
        return McpInventory(
            servers=tuple(entries),
            user_config_path=str(home_config_path()),
            project_config_path=str(project_path) if project_path is not None else None,
        )

    def list_presets(self, project_id: str | None = None) -> McpPresetInventory:
        configured = {server.name for server in self.list(project_id).servers}
        return McpPresetInventory(
            presets=tuple(
                self._preset_info(preset, configured=preset.id in configured)
                for preset in self._presets.list()
            ),
            source=self._presets.bundle.source,
            source_revision=self._presets.bundle.source_revision,
        )

    def add_preset(
        self,
        preset_id: str,
        *,
        project_id: str | None = None,
        enabled: bool = False,
    ) -> McpInventory:
        preset = self._presets.get(preset_id)
        if any(server.name == preset.id for server in self.list(project_id).servers):
            raise InvalidArgumentError(f"MCP server is already configured: {preset.id}")
        definition = self._presets.materialize(preset_id, enabled=enabled)
        if definition.description is None:
            definition = definition.model_copy(
                update={"description": preset.description}
            )
        result = self.upsert(
            name=preset.id,
            patch=definition.model_dump(
                by_alias=True,
                exclude_none=True,
                exclude_defaults=True,
            ),
            scope="user",
            project_id=project_id,
        )
        return result

    def set_enabled(
        self,
        identifier: str,
        *,
        enabled: bool,
        project_id: str | None = None,
    ) -> McpInventory:
        """Enable or disable one configured server through its owning scope."""

        matches = tuple(
            server
            for server in self.list(project_id).servers
            if server.id == identifier or server.name == identifier
        )
        if not matches:
            raise InvalidArgumentError(f"unknown MCP server: {identifier}")
        if len(matches) > 1:
            raise InvalidArgumentError(
                f"MCP server name is ambiguous; use its ID: {identifier}"
            )
        server = matches[0]
        if server.source == "plugin":
            raise InvalidArgumentError(
                "Plugin MCP servers are enabled through their Plugin policy"
            )
        return self.upsert(
            name=server.name,
            patch={"enabled": enabled},
            scope="project" if server.source == "project" else "user",
            project_id=project_id,
        )

    def probe(
        self,
        name: str,
        *,
        project_id: str | None = None,
    ) -> McpProbeResult:
        server, workspace = self._resolve_server(name, project_id)
        self._set_runtime(
            workspace,
            server.server_id,
            _RuntimeSnapshot("connecting", "Testing MCP handshake"),
        )
        result = asyncio.run(
            probe_mcp_server(
                server,
                credential_resolver=(
                    (
                        lambda connection_id: self._credential_resolver(
                            connection_id,
                            project_id,
                        )
                    )
                    if self._credential_resolver is not None
                    else None
                ),
                oauth_provider_factory=create_mcp_oauth_provider,
            )
        )
        self._set_runtime(
            workspace,
            server.server_id,
            _RuntimeSnapshot(
                "tested" if result.ok else "failed",
                (
                    "Connection test passed; the one-shot connection is closed"
                    if result.ok
                    else result.error or "Connection test failed"
                ),
                result.tool_count,
                result.resource_count,
                result.prompt_count,
            ),
        )
        return result

    def oauth_start(
        self,
        name: str,
        *,
        project_id: str | None = None,
        open_browser: bool = True,
        reset_credentials: bool = False,
    ) -> McpOAuthFlowInfo:
        server, _workspace = self._resolve_server(name, project_id)
        return self._oauth.start(
            server,
            open_browser=open_browser,
            reset_credentials=reset_credentials,
        )

    def oauth_logout(
        self,
        name: str,
        *,
        project_id: str | None = None,
    ) -> bool:
        server, workspace = self._resolve_server(name, project_id)
        removed = self._oauth.logout(server)
        self._set_runtime(
            workspace,
            server.server_id,
            _RuntimeSnapshot("stopped", "OAuth credentials removed"),
        )
        return removed

    def oauth_cancel(
        self,
        name: str,
        *,
        project_id: str | None = None,
    ) -> bool:
        server, _workspace = self._resolve_server(name, project_id)
        self._oauth.cancel(server)
        return True

    def oauth_wait(self, flow_id: str, *, timeout: float = 300) -> McpOAuthFlowInfo:
        return self._oauth.wait(flow_id, timeout=timeout)

    def publish_runtime_status(self, workspace: str | Path, statuses) -> None:
        root = Path(workspace).expanduser().resolve(strict=False)
        for status in statuses:
            state = {
                "configured": "stopped",
                "starting": "connecting",
                "ready": "connected",
                "closed": "stopped",
            }.get(status.state, status.state)
            self._set_runtime(
                root,
                status.server_id,
                _RuntimeSnapshot(
                    state,
                    status.error or state.replace("_", " ").title(),
                    status.tool_count,
                ),
                publish=False,
            )
        self._publish_change()

    def upsert(
        self,
        *,
        name: str,
        patch: dict[str, Any],
        scope: str,
        project_id: str | None = None,
    ) -> McpInventory:
        try:
            clean_name = validate_server_name(name)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        unknown = set(patch) - _SERVER_FIELDS
        if unknown:
            raise InvalidArgumentError(
                f"unsupported MCP server field(s): {', '.join(sorted(unknown))}"
            )
        if not patch:
            raise InvalidArgumentError("MCP server patch must not be empty")
        store = self._store(scope, project_id)
        previous = _raw_servers(store.read()).get(clean_name)

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            selected = _raw_servers(current)
            existing = selected.get(clean_name, {})
            base = dict(existing) if isinstance(existing, dict) else {}
            candidate = deep_merge(base, patch)
            if not base and "enabled" not in candidate:
                candidate["enabled"] = False
            if patch.get("type") == "stdio":
                for field in (
                    "url",
                    "auth",
                    "headers",
                    "envHttpHeaders",
                    "envUrlParams",
                    "bearerTokenEnvVar",
                    "bearerTokenCredential",
                ):
                    if field not in patch:
                        candidate.pop(field, None)
            elif patch.get("type") in {"sse", "streamableHttp"}:
                for field in (
                    "command",
                    "args",
                    "cwd",
                    "env",
                    "envVars",
                    "requiredEnvVars",
                    "credentialEnv",
                ):
                    if field not in patch:
                        candidate.pop(field, None)
            definition = _validate_definition(candidate)
            if scope == "project" and (
                definition.credential_env or definition.bearer_token_credential
            ):
                raise InvalidArgumentError(
                    "Project MCP servers cannot request stored provider credentials; "
                    "configure the server at user scope"
                )
            selected[clean_name] = candidate
            return _replace_servers(current, selected)

        store.mutate(mutate)
        _remove_replaced_oauth_credentials(clean_name, previous, patch)
        self._publish_change()
        return self.list(project_id)

    def remove(
        self,
        *,
        name: str,
        scope: str,
        project_id: str | None = None,
    ) -> McpInventory:
        try:
            clean_name = validate_server_name(name)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
        store = self._store(scope, project_id)
        previous = _raw_servers(store.read()).get(clean_name)

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            selected = _raw_servers(current)
            if clean_name not in selected:
                raise InvalidArgumentError(
                    f"MCP server is not defined in the {scope} config: {clean_name}"
                )
            selected.pop(clean_name)
            return _replace_servers(current, selected)

        store.mutate(mutate)
        _remove_oauth_credentials(clean_name, previous)
        self._publish_change()
        return self.list(project_id)

    def _resolve_server(
        self,
        identifier: str,
        project_id: str | None,
    ) -> tuple[ResolvedMcpServer, Path]:
        project = self.projects.read(project_id) if project_id is not None else None
        workspace = (
            Path(project.canonical_path).resolve(strict=True)
            if project is not None
            else Path.cwd().resolve(strict=False)
        )
        plugin_servers = self._plugin_servers(workspace) if self._plugin_servers else ()
        plan = McpConfigResolver().resolve(
            workspace,
            project_trusted=(
                project is not None and project.trust_state is TrustState.TRUSTED
            ),
            plugin_servers=plugin_servers,
            include_disabled=True,
        )
        matches = tuple(
            server
            for server in plan.servers
            if server.server_id == identifier or server.name == identifier
        )
        if not matches:
            raise InvalidArgumentError(f"unknown MCP server: {identifier}")
        if len(matches) > 1:
            raise InvalidArgumentError(
                f"MCP server name is ambiguous; use its ID: {identifier}"
            )
        return matches[0], workspace

    def _decorate_status(self, info: McpServerInfo, workspace: Path) -> McpServerInfo:
        with self._lock:
            snapshot = self._runtime.get((str(workspace), info.id))
        auth_state = "not_required"
        if info.auth == "oauth" and info.url:
            auth_state = self._oauth.status_for(info.id, info.name, info.url)
        if snapshot is None:
            snapshot = _RuntimeSnapshot("stopped", "Not connected in this process")
        return replace(
            info,
            auth_state=auth_state,
            runtime_state=snapshot.state,
            runtime_message=snapshot.message,
            tool_count=snapshot.tool_count,
            resource_count=snapshot.resource_count,
            prompt_count=snapshot.prompt_count,
        )

    def _preset_info(self, preset: McpPreset, *, configured: bool) -> McpPresetInfo:
        required = preset.required_environment
        return McpPresetInfo(
            preset.id,
            preset.display_name,
            preset.category,
            preset.description,
            preset.docs_url,
            preset.server.type,
            preset.server.auth,
            preset.requires,
            preset.note,
            required,
            tuple(name for name in required if not os.environ.get(name)),
            configured,
        )

    def _set_runtime(
        self,
        workspace: Path,
        server_id: str,
        status: _RuntimeSnapshot,
        *,
        publish: bool = True,
    ) -> None:
        with self._lock:
            self._runtime[(str(workspace.resolve(strict=False)), server_id)] = status
        if publish:
            self._publish_change()

    def _on_oauth_change(self, _flow: McpOAuthFlowInfo) -> None:
        self._publish_change()

    def _publish_change(self) -> None:
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - event listener boundary
                logger.exception("MCP change listener failed")

    def _store(self, scope: str, project_id: str | None) -> ConfigStore:
        if scope == "user":
            return ConfigStore(home_config_path())
        if scope != "project":
            raise InvalidArgumentError("MCP config scope must be user or project")
        if project_id is None:
            raise InvalidArgumentError("projectId is required for project MCP config")
        project = self.projects.read(project_id)
        if project.trust_state is not TrustState.TRUSTED:
            raise ProjectNotTrustedError(
                "project must be trusted before its MCP config can be changed"
            )
        workspace = Path(project.canonical_path).resolve(strict=True)
        return ConfigStore(workspace / "deepcode_config.json")


def _raw_servers(config: dict[str, Any]) -> dict[str, Any]:
    servers = config.get("mcpServers", config.get("mcp_servers", {}))
    return dict(servers) if isinstance(servers, dict) else {}


def _replace_servers(
    current: dict[str, Any], servers: dict[str, Any]
) -> dict[str, Any]:
    updated = {key: value for key, value in current.items() if key != "mcp_servers"}
    if servers:
        updated["mcpServers"] = servers
    else:
        updated.pop("mcpServers", None)
    return updated


def _validate_definition(raw: dict[str, Any]) -> McpServerDefinition:
    try:
        definition = McpServerDefinition.model_validate(raw)
        validate_no_literal_secrets(definition)
        return definition
    except (ValidationError, ValueError, TypeError) as exc:
        raise InvalidArgumentError(f"invalid MCP server configuration: {exc}") from exc


def _remove_replaced_oauth_credentials(
    name: str,
    previous: Any,
    patch: dict[str, Any],
) -> None:
    if not isinstance(previous, dict) or previous.get("auth") != "oauth":
        return
    previous_url = previous.get("url")
    if not isinstance(previous_url, str) or not previous_url:
        return
    next_auth = patch.get("auth", previous.get("auth"))
    next_url = patch.get("url", previous_url)
    if next_auth != "oauth" or next_url != previous_url:
        McpOAuthCredentialStore.delete_endpoint(name, previous_url)


def _remove_oauth_credentials(name: str, previous: Any) -> None:
    if not isinstance(previous, dict) or previous.get("auth") != "oauth":
        return
    previous_url = previous.get("url")
    if isinstance(previous_url, str) and previous_url:
        McpOAuthCredentialStore.delete_endpoint(name, previous_url)


def _server_info(
    name: str,
    raw: Any,
    *,
    source: str,
    config_dir: Path,
    validate_literals: bool = True,
) -> McpServerInfo:
    if not isinstance(raw, dict):
        return _invalid_server_info(name, {}, source, "Server entry must be an object")
    try:
        server = McpServerDefinition.model_validate(raw)
        if validate_literals:
            validate_no_literal_secrets(server)
    except (ValidationError, ValueError, TypeError) as exc:
        return _invalid_server_info(name, raw, source, str(exc))
    return _validated_server_info(
        name,
        server,
        server_id=name,
        source=source,
        config_dir=config_dir,
    )


def _resolved_server_info(server: ResolvedMcpServer) -> McpServerInfo:
    return _validated_server_info(
        server.name,
        server.definition,
        server_id=server.server_id,
        plugin_id=server.plugin_id,
        policy_key=server.policy_key,
        source=server.source.value,
        config_dir=server.config_dir,
    )


def _validated_server_info(
    name: str,
    server: McpServerDefinition,
    *,
    server_id: str,
    plugin_id: str | None = None,
    policy_key: str | None = None,
    source: str,
    config_dir: Path,
) -> McpServerInfo:
    state, message = _configuration_state(server, config_dir=config_dir)
    return McpServerInfo(
        id=server_id,
        name=name,
        plugin_id=plugin_id,
        policy_key=policy_key,
        transport=server.type,
        command=server.command,
        args=tuple(_redact_args(list(server.args))),
        cwd=server.cwd,
        url=server.url,
        auth=server.auth,
        enabled=server.enabled,
        required=server.required,
        enabled_tools=server.enabled_tools,
        disabled_tools=server.disabled_tools,
        startup_timeout_seconds=server.startup_timeout_seconds,
        tool_timeout_seconds=server.tool_timeout_seconds,
        approval_mode=server.approval_mode.value,
        description=server.description,
        env_keys=tuple(sorted(server.env)),
        forwarded_env_keys=tuple(sorted(server.env_vars)),
        required_env_keys=tuple(
            sorted({*server.required_env_vars, *server.env_url_params.values()})
        ),
        missing_env_keys=tuple(
            sorted(
                name
                for name in {
                    *server.required_env_vars,
                    *server.env_url_params.values(),
                }
                if not os.environ.get(name)
            )
        ),
        credential_env_keys=tuple(sorted(server.credential_env)),
        header_keys=tuple(
            sorted(
                {
                    *server.headers,
                    *server.env_http_headers,
                    *(
                        ["Authorization"]
                        if server.bearer_token_env_var or server.bearer_token_credential
                        else []
                    ),
                }
            )
        ),
        source=source,
        configuration_state=state,
        configuration_message=message,
        auth_state="not_required",
        runtime_state="stopped",
        runtime_message="Not connected in this process",
        tool_count=0,
        resource_count=0,
        prompt_count=0,
    )


def _invalid_server_info(
    name: str,
    raw: dict[str, Any],
    source: str,
    error: str,
) -> McpServerInfo:
    args = raw.get("args") if isinstance(raw.get("args"), list) else []
    return McpServerInfo(
        id=name,
        name=name,
        plugin_id=None,
        policy_key=None,
        transport=str(raw.get("type") or "invalid"),
        command=raw.get("command") if isinstance(raw.get("command"), str) else None,
        args=tuple(_redact_args([str(item) for item in args])),
        cwd=raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
        url=raw.get("url") if isinstance(raw.get("url"), str) else None,
        auth=raw.get("auth") if raw.get("auth") == "oauth" else None,
        enabled=raw.get("enabled", True) is not False,
        required=raw.get("required", False) is True,
        enabled_tools=None,
        disabled_tools=(),
        startup_timeout_seconds=0,
        tool_timeout_seconds=0,
        approval_mode="auto",
        description=None,
        env_keys=tuple(sorted(raw.get("env", {})))
        if isinstance(raw.get("env"), dict)
        else (),
        forwarded_env_keys=(),
        required_env_keys=(),
        missing_env_keys=(),
        credential_env_keys=(),
        header_keys=tuple(sorted(raw.get("headers", {})))
        if isinstance(raw.get("headers"), dict)
        else (),
        source=source,
        configuration_state="invalid",
        configuration_message=error[:1_000],
        auth_state="not_required",
        runtime_state="stopped",
        runtime_message="Configuration is invalid",
        tool_count=0,
        resource_count=0,
        prompt_count=0,
    )


def _configuration_state(
    server: McpServerDefinition,
    *,
    config_dir: Path,
) -> tuple[str, str]:
    if not server.enabled:
        return "disabled", "Server is disabled"
    missing_environment = tuple(
        sorted(
            name
            for name in {
                *server.required_env_vars,
                *server.env_url_params.values(),
            }
            if not os.environ.get(name)
        )
    )
    if missing_environment:
        return (
            "missing_credentials",
            "Missing environment variable(s): " + ", ".join(missing_environment),
        )
    if server.type == "stdio":
        command = server.command or ""
        if "/" in command or "\\" in command:
            candidate = Path(command).expanduser()
            if not candidate.is_absolute():
                candidate = config_dir / candidate
            if not candidate.is_file():
                return "invalid", "Configured command path does not exist"
        elif shutil.which(command) is None:
            return "invalid", "Configured command is not on PATH"
    else:
        parsed = urlparse(server.url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "invalid", "Configured URL is not a valid http(s) endpoint"
    return "configured", "Configuration is ready; connection is checked on use"


def _redact_args(arguments: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for argument in arguments:
        if hide_next:
            redacted.append("••••••")
            hide_next = False
            continue
        if "=" in argument:
            flag, _value = argument.split("=", 1)
            redacted.append(f"{flag}=••••••" if _SECRET_FLAG.search(flag) else argument)
            continue
        redacted.append(argument)
        hide_next = argument.startswith("-") and bool(_SECRET_FLAG.search(argument))
    return redacted


__all__ = ["McpInventory", "McpServerInfo", "McpService"]
