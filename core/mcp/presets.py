"""Validated, package-owned MCP server templates.

The catalog is data, not product logic: adding or updating an integration does
not require touching the runtime, CLI, TUI, or Desktop. Adding a preset copies
one ordinary :class:`McpServerDefinition` into user configuration and leaves it
disabled until the user explicitly tests and enables it.
"""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field

from core.mcp.models import McpServerDefinition, validate_server_name


class _PresetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class McpPreset(_PresetModel):
    id: str
    display_name: str = Field(alias="displayName")
    category: str
    description: str
    docs_url: str = Field(alias="docsUrl")
    requires: str = ""
    note: str = ""
    server: McpServerDefinition

    @property
    def required_environment(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.server.required_env_vars,
                    *self.server.env_url_params.values(),
                )
            )
        )


class McpPresetBundle(_PresetModel):
    version: int
    source: str
    source_revision: str = Field(alias="sourceRevision")
    presets: tuple[McpPreset, ...]


class McpPresetCatalog:
    """Load the bundled catalog once and expose immutable definitions."""

    def __init__(self, bundle: McpPresetBundle | None = None) -> None:
        self.bundle = bundle or _load_bundle()
        by_id: dict[str, McpPreset] = {}
        for preset in self.bundle.presets:
            preset_id = validate_server_name(preset.id)
            if preset_id in by_id:
                raise ValueError(f"duplicate MCP preset ID: {preset_id}")
            by_id[preset_id] = preset
        self._by_id = by_id

    def list(self) -> tuple[McpPreset, ...]:
        return self.bundle.presets

    def get(self, preset_id: str) -> McpPreset:
        clean = validate_server_name(preset_id)
        try:
            return self._by_id[clean]
        except KeyError as exc:
            raise ValueError(f"unknown MCP preset: {clean}") from exc

    def materialize(
        self, preset_id: str, *, enabled: bool = False
    ) -> McpServerDefinition:
        preset = self.get(preset_id)
        return preset.server.model_copy(update={"enabled": enabled})


def _load_bundle() -> McpPresetBundle:
    resource = files("core.mcp").joinpath("presets.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return McpPresetBundle.model_validate(payload)


__all__ = ["McpPreset", "McpPresetBundle", "McpPresetCatalog"]
