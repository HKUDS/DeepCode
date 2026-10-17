"""MCP supply-chain audit: declaration review for the effective inventory.

Lesson 13's supply-chain warning: third-party components (Python modules,
external datasets — and for a harness, remote MCP servers) can be compromised.
This module renders a *declaration audit* for the effective MCP inventory: for
every server, what is being introduced (transport, source, command/URL) and
what posture it declares (enabled, tool filter, approval mode). Pure
mechanism — no network, no execution.

The output is designed for: (a) a human review before first use (``deepcode
mcp audit``), (b) a regression diff when a config changes (a newly appearing
server/tool in the diff is a supply-chain event worth noticing).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ServerAuditEntry:
    """One server's supply-chain declaration."""

    server_id: str
    name: str
    transport: str  # "stdio" | "sse" | "streamableHttp"
    enabled: bool = True
    command: str | None = None  # stdio executable (provenance)
    url: str | None = None  # remote endpoint
    approval_mode: str | None = None
    enabled_tools: tuple[str, ...] | None = None
    disabled_tools: tuple[str, ...] = field(default_factory=tuple)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["disabled_tools"]:
            payload.pop("disabled_tools")
        if not payload["notes"]:
            payload.pop("notes")
        return payload


@dataclass
class McpAuditReport:
    """Aggregate audit of an effective MCP inventory."""

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
        """Human-facing risk lines (supply-chain review checklist).

        Disabled servers register nothing at runtime, so they introduce no
        supply-chain exposure and produce no risk lines (mirrors the posture
        rules in ``cli.mcp_cli._posture_summary``).
        """
        risks: list[str] = []
        for server in self.servers:
            if not server.enabled:
                continue
            if server.transport == "stdio" and server.command:
                risks.append(
                    f"server '{server.name}' executes local command "
                    f"'{server.command}' (verify provenance)"
                )
            if server.transport in ("sse", "streamableHttp") and server.url:
                risks.append(
                    f"server '{server.name}' talks to remote endpoint "
                    f"'{server.url}' (verify trust)"
                )
            if not server.enabled_tools and not server.disabled_tools:
                risks.append(
                    f"server '{server.name}' exposes ALL its tools "
                    "with no explicit filter"
                )
        return risks


def audit_inventory(inventory: Any) -> McpAuditReport:
    """Build the audit for an effective MCP inventory.

    Accepts any object exposing ``servers`` (an iterable of entries with the
    ``McpServerInfo`` field names: id/name/transport/command/url/enabled/
    enabled_tools/disabled_tools/approval_mode/missing_env_keys) so it stays
    decoupled from the exact model type.
    """
    report = McpAuditReport()
    for server in getattr(inventory, "servers", None) or []:
        report.servers.append(
            ServerAuditEntry(
                server_id=str(getattr(server, "id", "") or ""),
                name=str(getattr(server, "name", "") or ""),
                transport=str(getattr(server, "transport", "unknown") or "unknown"),
                enabled=bool(getattr(server, "enabled", True)),
                command=getattr(server, "command", None),
                url=getattr(server, "url", None),
                approval_mode=_mode_name(getattr(server, "approval_mode", None)),
                enabled_tools=getattr(server, "enabled_tools", None),
                disabled_tools=tuple(getattr(server, "disabled_tools", None) or ()),
                notes=_server_notes(server),
            )
        )
    return report


def _mode_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


def _server_notes(server: Any) -> list[str]:
    notes: list[str] = []
    missing = tuple(getattr(server, "missing_env_keys", None) or ())
    if missing:
        notes.append("missing env keys: " + ", ".join(missing))
    return notes


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = [
    "McpAuditReport",
    "ServerAuditEntry",
    "audit_inventory",
]
