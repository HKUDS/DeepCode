"""P2-A7: tool-name miss semantic candidates (GenAI lesson 17)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_runtime.tools.base import Tool
from core.agent_runtime.tools.registry import ToolRegistry
from core.agent_runtime.tools.semantic_hint import build_miss_message, suggest_tools

_AVAILABLE = [
    "read",
    "read_file",
    "write",
    "write_file",
    "edit",
    "apply_patch",
    "grep",
    "glob",
    "bash",
    "web_fetch",
    "mcp__srv__search_docs",
]


def test_suggests_close_names_by_token_overlap():
    candidates = suggest_tools("read_fiel", _AVAILABLE)
    assert "read_file" in candidates
    assert candidates[0] == "read_file"


def test_suggests_underscore_variant():
    candidates = suggest_tools("readfile", _AVAILABLE)
    assert "read_file" in candidates


def test_below_threshold_returns_empty():
    assert suggest_tools("zzz_nothing_like_this", _AVAILABLE) == []


def test_mcp_prefixed_candidate_found():
    candidates = suggest_tools("search_docs", _AVAILABLE)
    assert any(c.startswith("mcp__srv__") for c in candidates)


def test_build_miss_message_with_candidates():
    msg = build_miss_message("read_fiel", _AVAILABLE)
    assert "not found" in msg
    assert "Did you mean" in msg
    assert "read_file" in msg


def test_build_miss_message_without_candidates():
    msg = build_miss_message("totally_unknown", _AVAILABLE)
    assert "not found" in msg
    assert "Did you mean" not in msg


def test_registry_miss_includes_semantic_hint():
    registry = ToolRegistry()
    registry.register(_NoopTool("read_file"))
    registry.register(_NoopTool("write_file"))
    _tool, _params, error = registry.prepare_call("read_fiel", {})
    assert error is not None
    assert "Did you mean" in error
    assert "read_file" in error


class _NoopTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"does {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **_kwargs):
        return "ok"
