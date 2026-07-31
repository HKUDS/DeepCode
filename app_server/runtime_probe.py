"""Deterministic import probe for packaged App Server capabilities."""

from __future__ import annotations

import importlib
from typing import Any

from core.version import __version__

RUNTIME_MODULES = (
    "core.agent_setup",
    "core.providers.anthropic",
    "core.providers.openai_compat",
    "mcp.client.session",
    "mcp.client.sse",
    "mcp.client.stdio",
    "mcp.client.streamable_http",
    "pypdf",
    "tools.document_conversion",
    "tools.pdf_downloader",
    "workflows.agent_orchestration_engine",
)


def verify_runtime() -> dict[str, Any]:
    """Import every lazy capability that a packaged Desktop can activate."""

    for module in RUNTIME_MODULES:
        importlib.import_module(module)
    return {
        "ok": True,
        "modules": list(RUNTIME_MODULES),
        "providers": ["anthropic", "openai_compat"],
        "paperFallback": "pypdf",
        "documentFormats": ["pdf", "md", "markdown", "txt", "docx", "html", "htm"],
        "version": __version__,
    }
