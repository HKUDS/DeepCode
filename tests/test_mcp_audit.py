"""P2-E3: MCP supply-chain audit (GenAI lesson 13)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mcp.audit import audit_plan


def _definition(**kw):
    base = {
        "type": "stdio",
        "command": "node",
        "url": None,
        "approval_mode": None,
        "enabled_tools": None,
        "disabled_tools": (),
        "read_only_tools": (),
        "required_env_vars": (),
        "supports_parallel_tool_calls": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _resolved(server_id, name, definition, source="user"):
    server = SimpleNamespace(
        server_id=server_id, name=name, source=source, definition=definition
    )
    return SimpleNamespace(server=server)


def _plan(*resolved):
    return SimpleNamespace(servers=list(resolved))


def test_audit_lists_server_declarations():
    plan = _plan(
        _resolved("srv1", "code-server", _definition(enabled_tools=("read", "write"))),
    )
    report = audit_plan(plan)
    assert len(report.servers) == 1
    entry = report.servers[0]
    assert entry.server_id == "srv1"
    assert entry.transport == "stdio"
    assert entry.command == "node"
    assert entry.tools == ["read", "write"]
    assert entry.tool_count == 2


def test_audit_notes_remote_endpoint_risk():
    plan = _plan(
        _resolved(
            "srv2",
            "remote",
            _definition(type="http", url="https://evil.example/mcp"),
        ),
    )
    report = audit_plan(plan)
    risks = report.risks()
    assert any("remote endpoint" in r and "evil.example" in r for r in risks)


def test_audit_flags_unfiltered_all_tools():
    plan = _plan(_resolved("srv3", "broad", _definition()))
    risks = audit_plan(plan).risks()
    assert any("exposes ALL its tools" in r for r in risks)


def test_audit_allowlist_status_reflects_env(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", "trusted")
    plan = _plan(
        _resolved("trusted", "good", _definition()),
        _resolved("evil", "bad", _definition()),
    )
    report = audit_plan(plan)
    by_id = {s.server_id: s for s in report.servers}
    assert by_id["trusted"].allowlisted is True
    assert by_id["evil"].allowlisted is False
    risks = report.risks()
    assert any("NOT on the P1-9 allowlist" in r for r in risks)


def test_audit_json_roundtrip():
    plan = _plan(_resolved("s1", "n", _definition(enabled_tools=("t",))))
    import json

    payload = json.loads(audit_plan(plan).to_json())
    assert payload["server_count"] == 1
    assert payload["servers"][0]["name"] == "n"


def test_audit_empty_plan():
    assert audit_plan(_plan()).servers == []
