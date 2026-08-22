"""P1-9: MCP server allowlist (GenAI lesson 13, supply-chain hardening).

Remote MCP servers are the harness's widest third-party exposure surface —
a compromised server can register arbitrary tools (lesson 13: supply-chain
vulnerabilities). ``DEEPCODE_MCP_SERVER_ALLOWLIST`` gates which servers get
registered at all. Empty = all allowed (default, no behavior change).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mcp.naming import (
    allowlist_env,
    server_allowed,
    visible_tool_name,
)

# ---- server_allowed ---------------------------------------------------------


def test_default_allows_everything(monkeypatch):
    monkeypatch.delenv("DEEPCODE_MCP_SERVER_ALLOWLIST", raising=False)
    assert server_allowed("any-server-id") is True
    assert server_allowed("srv-a", "Server A") is True


def test_allowlist_blocks_unlisted_server(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", "trusted-srv")
    assert server_allowed("trusted-srv") is True
    assert server_allowed("evil-srv") is False


def test_allowlist_matches_by_name_alias(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", "My Trusted Server")
    assert server_allowed("generated-id-123", "My Trusted Server") is True
    assert server_allowed("generated-id-123", "Other Server") is False


def test_allowlist_multiple_entries(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", "a, b ,c")
    assert server_allowed("a") is True
    assert server_allowed("b") is True
    assert server_allowed("c") is True
    assert server_allowed("d") is False


def test_allowlist_blank_entries_ignored(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", ",  ,")
    assert server_allowed("anything") is True  # no real entries → allow all


def test_allowlist_env_reports_raw_value(monkeypatch):
    monkeypatch.setenv("DEEPCODE_MCP_SERVER_ALLOWLIST", "x, y")
    assert allowlist_env() == "x, y"


# ---- naming contract stays intact -------------------------------------------


def test_visible_tool_name_still_mcp_prefixed():
    used: set[str] = set()
    name = visible_tool_name("srv1", "read_file", used=used)
    assert name.startswith("mcp__")
    assert name in used
