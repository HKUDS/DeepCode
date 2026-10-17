"""Supply-chain audit of the effective MCP inventory (pure mechanism)."""

from __future__ import annotations

from types import SimpleNamespace

from core.mcp.audit import audit_inventory


def _server(**overrides):
    base = {
        "id": "srv-1",
        "name": "fixture",
        "transport": "stdio",
        "command": "python3",
        "args": ("server.py",),
        "url": None,
        "enabled": True,
        "enabled_tools": None,
        "disabled_tools": (),
        "approval_mode": "auto",
        "missing_env_keys": (),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _inventory(*servers):
    return SimpleNamespace(servers=servers)


def test_stdio_server_reports_local_command_provenance() -> None:
    report = audit_inventory(_inventory(_server()))
    risks = report.risks()
    assert any("executes local command 'python3'" in risk for risk in risks)


def test_remote_server_reports_endpoint_trust() -> None:
    report = audit_inventory(
        _inventory(
            _server(
                transport="streamableHttp",
                command=None,
                url="https://mcp.example/rpc",
            )
        )
    )
    assert any(
        "remote endpoint 'https://mcp.example/rpc'" in risk for risk in report.risks()
    )


def test_unfiltered_server_is_flagged_and_filtered_is_not() -> None:
    unfiltered = audit_inventory(_inventory(_server()))
    assert any("exposes ALL its tools" in risk for risk in unfiltered.risks())
    filtered = audit_inventory(_inventory(_server(enabled_tools=("read_file",))))
    assert not any("exposes ALL" in risk for risk in filtered.risks())


def test_disabled_servers_produce_no_risks() -> None:
    report = audit_inventory(_inventory(_server(enabled=False)))
    assert report.risks() == []
    assert report.servers[0].enabled is False


def test_missing_env_keys_appear_as_notes() -> None:
    report = audit_inventory(_inventory(_server(missing_env_keys=("API_TOKEN",))))
    assert report.servers[0].notes == ["missing env keys: API_TOKEN"]


def test_report_serializes_with_trimmed_empty_fields() -> None:
    report = audit_inventory(_inventory(_server()))
    payload = report.to_dict()
    assert payload["server_count"] == 1
    assert payload["servers"][0]["server_id"] == "srv-1"
    assert payload["servers"][0]["transport"] == "stdio"
    assert "disabled_tools" not in payload["servers"][0]
    assert "notes" not in payload["servers"][0]
    assert report.to_json()
