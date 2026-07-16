"""Safe MCP configuration inventory and scoped, atomic mutations."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.application.config_store import ConfigStore, deep_merge
from core.application.errors import InvalidArgumentError, ProjectNotTrustedError
from core.application.project_service import ProjectService
from core.config import (
    MCPServerSchema,
    home_config_path,
    load_config,
    load_config_for_workspace,
    project_config_path,
)
from core.domain.project import TrustState


_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SERVER_FIELDS = {
    "type",
    "command",
    "args",
    "env",
    "url",
    "headers",
    "enabledTools",
    "toolTimeout",
    "description",
}
_SECRET_FLAG = re.compile(
    r"(?:token|api[-_]?key|password|passwd|secret|authorization|auth|header)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class McpServerInfo:
    name: str
    transport: str
    command: str | None
    args: tuple[str, ...]
    url: str | None
    enabled_tools: tuple[str, ...]
    tool_timeout: int
    description: str | None
    env_keys: tuple[str, ...]
    header_keys: tuple[str, ...]
    source: str
    configuration_state: str
    configuration_message: str


@dataclass(frozen=True, slots=True)
class McpInventory:
    servers: tuple[McpServerInfo, ...]
    user_config_path: str
    project_config_path: str | None


class McpService:
    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def list(self, project_id: str | None = None) -> McpInventory:
        workspace = self._workspace(project_id)
        config = (
            load_config_for_workspace(workspace)
            if workspace is not None
            else load_config(config_path=home_config_path())
        )
        user_raw = ConfigStore(home_config_path()).read()
        project_path = project_config_path(workspace) if workspace is not None else None
        project_raw = (
            ConfigStore(project_path).read()
            if project_path is not None and project_path.is_file()
            else {}
        )
        user_names = set(_raw_servers(user_raw))
        project_names = set(_raw_servers(project_raw))
        servers = tuple(
            _server_info(
                name,
                server,
                source=(
                    "project"
                    if name in project_names
                    else "user"
                    if name in user_names
                    else "default"
                ),
            )
            for name, server in sorted(config.tools.mcp_servers.items())
        )
        return McpInventory(
            servers=servers,
            user_config_path=str(home_config_path()),
            project_config_path=str(project_path) if project_path is not None else None,
        )

    def upsert(
        self,
        *,
        name: str,
        patch: dict[str, Any],
        scope: str,
        project_id: str | None = None,
    ) -> McpInventory:
        clean_name = _validate_name(name)
        unknown = set(patch) - _SERVER_FIELDS
        if unknown:
            raise InvalidArgumentError(
                f"unsupported MCP server field(s): {', '.join(sorted(unknown))}"
            )
        if not patch:
            raise InvalidArgumentError("MCP server patch must not be empty")
        store, workspace = self._store(scope, project_id)

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            selected = dict(_raw_servers(current))
            base = self._effective_raw_server(
                clean_name,
                workspace if scope == "project" else None,
            )
            candidate = deep_merge(base, patch)
            try:
                validated = MCPServerSchema.model_validate(candidate)
            except Exception as exc:
                raise InvalidArgumentError(
                    f"invalid MCP server configuration: {exc}"
                ) from exc
            _validate_endpoint(validated)
            selected[clean_name] = candidate
            tools = current.get("tools")
            updated_tools = dict(tools) if isinstance(tools, dict) else {}
            updated_tools.pop("mcp_servers", None)
            updated_tools["mcpServers"] = selected
            return {**current, "tools": updated_tools}

        store.mutate(mutate)
        return self.list(project_id)

    def remove(
        self,
        *,
        name: str,
        scope: str,
        project_id: str | None = None,
    ) -> McpInventory:
        clean_name = _validate_name(name)
        store, _workspace = self._store(scope, project_id)

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            selected = dict(_raw_servers(current))
            if clean_name not in selected:
                raise InvalidArgumentError(
                    f"MCP server is not defined in the {scope} config: {clean_name}"
                )
            selected.pop(clean_name)
            tools = current.get("tools")
            updated_tools = dict(tools) if isinstance(tools, dict) else {}
            updated_tools.pop("mcp_servers", None)
            updated_tools["mcpServers"] = selected
            return {**current, "tools": updated_tools}

        store.mutate(mutate)
        return self.list(project_id)

    def _workspace(self, project_id: str | None) -> Path | None:
        if project_id is None:
            return None
        return Path(self.projects.read(project_id).canonical_path).resolve(strict=True)

    def _store(
        self, scope: str, project_id: str | None
    ) -> tuple[ConfigStore, Path | None]:
        if scope == "user":
            return ConfigStore(home_config_path()), self._workspace(project_id)
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
        return ConfigStore(workspace / "deepcode_config.json"), workspace

    @staticmethod
    def _effective_raw_server(name: str, workspace: Path | None) -> dict[str, Any]:
        user = _raw_servers(ConfigStore(home_config_path()).read()).get(name, {})
        project: dict[str, Any] = {}
        if workspace is not None:
            path = project_config_path(workspace)
            if path.is_file():
                project = _raw_servers(ConfigStore(path).read()).get(name, {})
        base = user if isinstance(user, dict) else {}
        override = project if isinstance(project, dict) else {}
        return deep_merge(base, override)


def _raw_servers(config: dict[str, Any]) -> dict[str, Any]:
    tools = config.get("tools")
    if not isinstance(tools, dict):
        return {}
    servers = tools.get("mcpServers", tools.get("mcp_servers", {}))
    return dict(servers) if isinstance(servers, dict) else {}


def _validate_name(name: str) -> str:
    clean = name.strip()
    if not _SERVER_NAME.fullmatch(clean):
        raise InvalidArgumentError(
            "MCP server name must be 1-80 letters, numbers, dots, dashes, or underscores"
        )
    return clean


def _validate_endpoint(server: MCPServerSchema) -> None:
    transport = server.type or ("stdio" if server.command else "streamableHttp")
    if transport == "stdio" and not server.command.strip():
        raise InvalidArgumentError("stdio MCP servers require a command")
    if transport in {"sse", "streamableHttp"}:
        parsed = urlparse(server.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidArgumentError("HTTP MCP servers require an http(s) URL")


def _server_info(name: str, server: MCPServerSchema, *, source: str) -> McpServerInfo:
    transport = server.type or (
        "stdio"
        if server.command
        else "sse"
        if server.url.rstrip("/").endswith("/sse")
        else "streamableHttp"
    )
    state, message = _configuration_state(server, transport)
    return McpServerInfo(
        name=name,
        transport=transport,
        command=server.command or None,
        args=tuple(_redact_args(server.args)),
        url=server.url or None,
        enabled_tools=tuple(server.enabled_tools),
        tool_timeout=server.tool_timeout,
        description=server.description,
        env_keys=tuple(sorted(server.env)),
        header_keys=tuple(sorted(server.headers)),
        source=source,
        configuration_state=state,
        configuration_message=message,
    )


def _configuration_state(server: MCPServerSchema, transport: str) -> tuple[str, str]:
    if transport == "stdio":
        command = server.command.strip()
        if not command:
            return "invalid", "Missing stdio command"
        if "/" in command or "\\" in command:
            if not Path(command).expanduser().is_file():
                return "invalid", "Configured command path does not exist"
        elif shutil.which(command) is None:
            return "invalid", "Configured command is not on PATH"
        return "configured", "Configuration is ready; connection is checked on use"
    parsed = urlparse(server.url)
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
            if _SECRET_FLAG.search(flag):
                redacted.append(f"{flag}=••••••")
                continue
            redacted.append(argument)
            continue
        redacted.append(argument)
        hide_next = argument.startswith("-") and bool(_SECRET_FLAG.search(argument))
    return redacted
