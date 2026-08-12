"""Agent Plugins 1.0 MCP component parser and runtime contribution adapter."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import ValidationError

from core.mcp.models import McpServerDefinition
from core.plugins.domain import (
    PluginComponentKind,
    PluginDiagnostic,
    PluginDiagnosticSeverity,
    PluginValidationError,
)

MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
MAX_MCP_CONFIG_BYTES = 256 * 1024
_PORTABLE_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UNSAFE_SERVER_ID = re.compile(r"[^A-Za-z0-9._-]+")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_STDIO_FIELDS = frozenset(("type", "command", "args", "env", "cwd"))
_HTTP_FIELDS = frozenset(("type", "url", "headers"))


@dataclass(frozen=True, slots=True)
class AgentPluginMcpTemplate:
    name: str
    definition: McpServerDefinition


@dataclass(frozen=True, slots=True)
class AgentPluginMcpResult:
    servers: tuple[AgentPluginMcpTemplate, ...]
    diagnostics: tuple[PluginDiagnostic, ...]
    fatal: bool = False
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class _JsonObject:
    pairs: tuple[tuple[str, Any], ...]


def load_agent_plugin_mcp(root: Path, *, plugin_schema: str) -> AgentPluginMcpResult:
    """Validate root ``mcp.json`` without launching or importing Plugin code."""

    path = root / "mcp.json"
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            raise ValueError("not a regular file")
        with resolved.open("rb") as stream:
            payload = stream.read(MAX_MCP_CONFIG_BYTES + 1)
    except (OSError, ValueError) as exc:
        return _fatal(f"Invalid Agent Plugins mcp.json component: {exc}")
    if len(payload) > MAX_MCP_CONFIG_BYTES:
        return _fatal(f"mcp.json exceeds {MAX_MCP_CONFIG_BYTES} bytes")
    revision = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=lambda pairs: _JsonObject(tuple(pairs)),
        )
        raw = _unique_object(decoded, label="mcp.json")
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PluginValidationError,
        TypeError,
    ) as exc:
        return _fatal(f"Invalid Agent Plugins mcp.json: {exc}", revision=revision)
    if not isinstance(raw, dict) or set(raw) != {"$schema", "mcpServers"}:
        return _fatal(
            "mcp.json must contain only $schema and mcpServers",
            revision=revision,
        )
    if raw.get("$schema") != MCP_SCHEMA:
        return _fatal(
            f"Unsupported Agent Plugins MCP schema: {raw.get('$schema')!r}",
            revision=revision,
        )
    if not plugin_schema.endswith("/1.0.0/plugin.schema.json"):
        return _fatal(
            "plugin.json and mcp.json target different specification versions",
            revision=revision,
        )
    try:
        servers = _unique_object(raw.get("mcpServers"), label="mcpServers")
    except (PluginValidationError, TypeError) as exc:
        return _fatal(str(exc), revision=revision)

    parsed: list[AgentPluginMcpTemplate] = []
    diagnostics: list[PluginDiagnostic] = []
    for name, value in servers.items():
        try:
            parsed.append(_parse_server(root, name, value))
        except (ValidationError, ValueError, TypeError) as exc:
            diagnostics.append(
                PluginDiagnostic(
                    code="agent_plugins.invalid_mcp_server",
                    severity=PluginDiagnosticSeverity.ERROR,
                    message=f"Skipped MCP server {name!r}: {exc}"[:2_000],
                    component=PluginComponentKind.MCP,
                    resource="mcp.json",
                )
            )
    return AgentPluginMcpResult(
        tuple(parsed),
        tuple(diagnostics),
        revision=revision,
    )


def _parse_server(root: Path, name: Any, value: Any) -> AgentPluginMcpTemplate:
    # JSON object member names are unrestricted by Agent Plugins 1.0. Runtime
    # IDs are normalized separately; rejecting a Unicode, empty, or path-like
    # member here would make the package parser non-conformant.
    if not isinstance(name, str):
        raise TypeError("server name must be a string")
    value = _unique_object(value, label=f"MCP server {name!r}")
    transport = value.get("type")
    if transport == "stdio":
        unknown = set(value) - _STDIO_FIELDS
        if unknown:
            raise ValueError(f"unknown stdio field: {min(unknown)}")
        command = value.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError("stdio command must be a non-empty executable token")
        _validate_plugin_command(root, command)
        environment = (
            _unique_object(value["env"], label="stdio env") if "env" in value else {}
        )
        if any(not isinstance(item, str) for item in environment.values()):
            raise ValueError("stdio env must contain string values")
        if any(_reserved_plugin_env(key) for key in environment):
            raise ValueError("stdio env cannot override PLUGIN_ROOT or PLUGIN_DATA")
        args = value.get("args", [])
        if not isinstance(args, list) or any(
            not isinstance(item, str) for item in args
        ):
            raise ValueError("stdio args must be a string array")
        cwd = value.get("cwd", "${PLUGIN_ROOT}")
        if not isinstance(cwd, str):
            raise ValueError("stdio cwd must be a string")
        _validate_plugin_cwd(root, cwd)
        normalized = {
            "type": "stdio",
            "command": command,
            "args": args,
            "env": environment,
            "cwd": cwd,
            "approvalMode": "writes",
        }
    elif transport in {"streamable-http", "sse"}:
        unknown = set(value) - _HTTP_FIELDS
        if unknown:
            raise ValueError(f"unknown HTTP field: {min(unknown)}")
        url = value.get("url")
        if not isinstance(url, str):
            raise ValueError("HTTP url must be a string")
        _validate_plugin_url(url)
        headers = (
            _unique_object(value["headers"], label="HTTP headers")
            if "headers" in value
            else {}
        )
        _validate_headers(headers)
        normalized = {
            "type": "streamableHttp" if transport == "streamable-http" else "sse",
            "url": url,
            "headers": headers,
            "approvalMode": "writes",
        }
    else:
        raise ValueError(f"unsupported MCP transport: {transport!r}")
    return AgentPluginMcpTemplate(
        name,
        McpServerDefinition.model_validate(normalized),
    )


def _validate_plugin_command(root: Path, command: str) -> None:
    if "${" in command:
        raise ValueError("command does not support placeholder expansion")
    if command.startswith("./"):
        _contained(root, root / command[2:], label="command")
        return
    if "/" in command or "\\" in command or command in {".", ".."}:
        raise ValueError("command must be a bare name or begin with ./")


def _validate_plugin_cwd(root: Path, cwd: str) -> None:
    data_probe = root.parent / ".plugin-data-containment-probe"
    if cwd.startswith("./"):
        _contained(root, root / cwd[2:], label="cwd")
        return
    for placeholder, base in (
        ("${PLUGIN_ROOT}", root),
        ("${PLUGIN_DATA}", data_probe),
    ):
        if cwd == placeholder or cwd.startswith(f"{placeholder}/"):
            suffix = cwd[len(placeholder) :].lstrip("/")
            _contained(base, base / suffix, label="cwd")
            return
    raise ValueError("cwd must begin with ./, ${PLUGIN_ROOT}, or ${PLUGIN_DATA}")


def _contained(base: Path, candidate: Path, *, label: str) -> None:
    resolved_base = base.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its permitted root") from exc


def _validate_plugin_url(value: str) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        raise ValueError(
            "url must be an absolute HTTP(S) URL without credentials or fragment"
        )
    if parsed.scheme == "http" and not _loopback(parsed.hostname):
        raise ValueError("non-loopback Agent Plugin MCP endpoints must use HTTPS")


def _loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_headers(value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError("headers must contain an object")
    seen: set[str] = set()
    for name, item in value.items():
        if not isinstance(name, str) or not _HTTP_HEADER_NAME.fullmatch(name):
            raise ValueError("headers contain an invalid field name")
        if not isinstance(item, str) or "\r" in item or "\n" in item:
            raise ValueError(f"header {name!r} contains an invalid value")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"duplicate case-insensitive header: {name}")
        seen.add(folded)


def _reserved_plugin_env(name: str) -> bool:
    if os.name == "nt":
        return name.casefold() in {"plugin_root", "plugin_data"}
    return name in {"PLUGIN_ROOT", "PLUGIN_DATA"}


def _fatal(message: str, *, revision: str | None = None) -> AgentPluginMcpResult:
    return AgentPluginMcpResult(
        (),
        (
            PluginDiagnostic(
                code="agent_plugins.invalid_mcp_component",
                severity=PluginDiagnosticSeverity.ERROR,
                message=message[:2_000],
                component=PluginComponentKind.MCP,
                resource="mcp.json",
            ),
        ),
        fatal=True,
        revision=revision,
    )


def _unique_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, _JsonObject):
        raise TypeError(f"{label} must contain an object")
    pairs = value.pairs
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PluginValidationError(f"Duplicate key in {label}: {key}")
        value[key] = item
    return value


def plugin_server_id(plugin_name: str, server_name: str) -> str:
    """Return a portable, stable runtime ID for an unrestricted JSON name."""

    cleaned = _UNSAFE_SERVER_ID.sub("_", server_name).strip("._-") or "server"
    candidate = f"{plugin_name}--{cleaned}"
    if (
        _PORTABLE_SERVER_NAME.fullmatch(server_name)
        and server_name == cleaned
        and len(candidate) <= 80
    ):
        return candidate
    digest = hashlib.sha256(f"{plugin_name}\0{server_name}".encode()).hexdigest()[:10]
    return f"{candidate[:69].rstrip('.-_')}.{digest}"


def plugin_policy_key(plugin_name: str, server_name: str) -> str:
    """Encode a reversible, log-safe user-policy address for a Plugin server."""

    return f"{plugin_name}/{quote(server_name, safe='-._~')}"


__all__ = [
    "MAX_MCP_CONFIG_BYTES",
    "MCP_SCHEMA",
    "AgentPluginMcpResult",
    "AgentPluginMcpTemplate",
    "load_agent_plugin_mcp",
    "plugin_policy_key",
    "plugin_server_id",
]
