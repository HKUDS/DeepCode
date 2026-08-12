"""Deterministic import probe for packaged App Server capabilities."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from core.mcp.presets import McpPresetCatalog
from core.skills import roots as skill_roots
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


def _verify_bundled_skills() -> list[str]:
    root = Path(skill_roots.__file__).resolve().parent / "builtin"
    manifest_path = root / "UPSTREAM_SOURCES.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sources = manifest["skills"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundled Skill source manifest is invalid") from exc
    if manifest.get("schemaVersion") != 1 or not isinstance(sources, dict):
        raise RuntimeError("bundled Skill source manifest is unsupported")

    expected = set(sources)
    discovered = {
        directory.name
        for directory in root.iterdir()
        if directory.is_dir() and (directory / "SKILL.md").is_file()
    }
    if discovered != expected:
        missing = sorted(expected - discovered)
        untracked = sorted(discovered - expected)
        raise RuntimeError(
            "bundled Skill inventory does not match its source manifest "
            f"(missing={missing}, untracked={untracked})"
        )
    return sorted(discovered)


def verify_runtime() -> dict[str, Any]:
    """Import every lazy capability that a packaged Desktop can activate."""

    for module in RUNTIME_MODULES:
        importlib.import_module(module)
    bundled_skills = _verify_bundled_skills()
    bundled_mcp_presets = [preset.id for preset in McpPresetCatalog().list()]
    return {
        "ok": True,
        "modules": list(RUNTIME_MODULES),
        "providers": ["anthropic", "openai_compat"],
        "paperFallback": "pypdf",
        "documentFormats": ["pdf", "md", "markdown", "txt", "docx", "html", "htm"],
        "bundledSkills": bundled_skills,
        "skillCreator": "skill-creator" in bundled_skills,
        "bundledMcpPresets": bundled_mcp_presets,
        "version": __version__,
    }
