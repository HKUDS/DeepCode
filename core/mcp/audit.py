"""P2-E3 (GenAI lesson 13): MCP supply-chain audit.

Lesson 13's supply-chain warning: third-party components (Python modules,
external datasets — and for a harness, remote MCP servers) can be
compromised. This module renders a *declaration audit* for a resolved MCP
plan: for every server, what is being introduced (transport, source,
command/URL), what capabilities it declares (tool count + names), and what
policy constrains it (approval mode, enabled/disabled tools, read-only hints,
allowlist status). Pure mechanism — no network, no execution.

The output is designed for: (a) a human review before first use, (b) a
regression diff when a config changes (a newly appearing server/tool in the
diff is a supply-chain event worth noticing), and (c) feeding the P1-9
allowlist decision.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from core.mcp.naming import server_allowed


@dataclass
class ServerAuditEntry:
    """One server's supply-chain declaration."""

    server_id: str
    name: str
    source: str  # "user" | "project" | "plugin" | ...
    transport: str  # "stdio" | "http" | "sse"
    command: str | None = None  # stdio executable (provenance)
    url: str | None = None  # http endpoint
    tool_count: int = 0
    tools: list[str] = field(default_factory=list)
    approval_mode: str | None = None
    enabled_tools: tuple[str, ...] | None = None
    disabled_tools: tuple[str, ...] = field(default_factory=tuple)
    allowlisted: bool = True  # P1-9: passes DEEPCODE_MCP_SERVER_ALLOWLIST
    read_only_tools: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["tools"]:
            payload.pop("tools")
        if not payload["disabled_tools"]:
            payload.pop("disabled_tools")
        if not payload["notes"]:
            payload.pop("notes")
        return payload


@dataclass
class McpAuditReport:
    """Aggregate audit of a resolved MCP plan."""

    servers: list[ServerAuditEntry] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "server_count": len(self.servers),
            "servers": [s.to_dict() for s in self.servers],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)

    def risks(self) -> list[str]:
        """Human-facing risk lines (supply-chain review checklist)."""
        risks: list[str] = []
        for server in self.servers:
            if not server.allowlisted:
                risks.append(
                    f"server '{server.name}' is NOT on the P1-9 allowlist — "
                    "it will register no tools"
                )
            if server.transport == "stdio" and server.command:
                risks.append(
                    f"server '{server.name}' executes local command "
                    f"'{server.command}' (verify provenance)"
                )
            if server.transport in ("http", "sse") and server.url:
                risks.append(
                    f"server '{server.name}' talks to remote endpoint "
                    f"'{server.url}' (verify trust)"
                )
            if not server.enabled_tools and not server.disabled_tools:
                risks.append(
                    f"server '{server.name}' exposes ALL its tools "
                    f"({server.tool_count}) with no explicit filter"
                )
        return risks


def audit_plan(plan: Any) -> McpAuditReport:
    """Build the audit for a resolved :class:`McpRuntimePlan`.

    Accepts any object exposing ``servers`` (an iterable of resolved servers
    with ``definition``) so it stays decoupled from the exact model type.
    """
    report = McpAuditReport()
    servers = getattr(plan, "servers", None) or []
    for resolved in servers:
        server = getattr(resolved, "server", None) or resolved
        definition = getattr(server, "definition", None)
        if definition is None:
            continue
        transport = str(getattr(definition, "type", "unknown") or "unknown")
        entry = ServerAuditEntry(
            server_id=str(getattr(server, "server_id", "") or ""),
            name=str(getattr(server, "name", "") or ""),
            source=str(getattr(server, "source", "unknown") or "unknown"),
            transport=transport,
            command=getattr(definition, "command", None),
            url=getattr(definition, "url", None),
            approval_mode=_mode_name(getattr(definition, "approval_mode", None)),
            enabled_tools=getattr(definition, "enabled_tools", None),
            disabled_tools=tuple(getattr(definition, "disabled_tools", None) or ()),
            allowlisted=server_allowed(
                str(getattr(server, "server_id", "") or ""),
                str(getattr(server, "name", "") or ""),
            ),
        )
        # Tool inventory comes from the definition's filters; tool_count is a
        # declared capability (the runtime discovers the real set at startup).
        declared = _declared_tools(definition)
        entry.tools = declared
        entry.tool_count = len(declared)
        notes = _definition_notes(definition)
        entry.notes = notes
        report.servers.append(entry)
    return report


def _mode_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


def _declared_tools(definition: Any) -> list[str]:
    enabled = getattr(definition, "enabled_tools", None)
    if enabled and "*" not in enabled:
        return list(enabled)
    disabled = tuple(getattr(definition, "disabled_tools", None) or ())
    if disabled:
        return ["* (all except: " + ", ".join(disabled) + ")"]
    return ["*"]


def _definition_notes(definition: Any) -> list[str]:
    notes: list[str] = []
    if getattr(definition, "read_only_tools", None):
        notes.append("declares read-only tool hints")
    if getattr(definition, "required_env_vars", None):
        notes.append("requires env vars: " + ", ".join(definition.required_env_vars))
    if getattr(definition, "supports_parallel_tool_calls", None) is False:
        notes.append("serial tool calls only")
    return notes


def _now_iso() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = [
    "McpAuditReport",
    "ServerAuditEntry",
    "audit_plan",
]
