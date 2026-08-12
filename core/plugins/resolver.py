"""Safe dispatch from a local package directory to a normalized PluginPackage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core.plugins.domain import PluginPackage, PluginValidationError, ResolvedPlugin
from core.plugins.formats import AGENT_PLUGIN_SCHEMA, AgentPluginsV1Adapter
from core.plugins.formats.agent_plugins_v1 import PLUGIN_MANIFEST_RESOURCE

MAX_PLUGIN_MANIFEST_BYTES = 64 * 1024

_ADAPTERS = {
    AGENT_PLUGIN_SCHEMA: AgentPluginsV1Adapter(),
}


def resolve_plugin(path: str | Path) -> ResolvedPlugin:
    """Resolve an Agent Plugin without activating any package component."""

    try:
        root = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PluginValidationError(f"Plugin directory is unavailable: {exc}") from exc
    if not root.is_dir():
        raise PluginValidationError("Plugin source must be a directory")

    manifest_path = root / PLUGIN_MANIFEST_RESOURCE.value
    try:
        resolved_manifest = manifest_path.resolve(strict=True)
        resolved_manifest.relative_to(root)
        if not resolved_manifest.is_file():
            raise ValueError("not a regular file")
        with resolved_manifest.open("rb") as stream:
            payload = stream.read(MAX_PLUGIN_MANIFEST_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise PluginValidationError(
            f"plugin.json is unavailable or outside the Plugin root: {exc}"
        ) from exc
    if len(payload) > MAX_PLUGIN_MANIFEST_BYTES:
        raise PluginValidationError(
            f"plugin.json exceeds {MAX_PLUGIN_MANIFEST_BYTES} bytes"
        )
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError as exc:
        raise PluginValidationError("plugin.json must be UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise PluginValidationError(f"Invalid plugin.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise PluginValidationError("plugin.json must contain a JSON object")

    schema = raw.get("$schema")
    if not isinstance(schema, str) or not schema:
        raise PluginValidationError(
            "plugin.json must declare a supported Agent Plugins $schema"
        )
    adapter = _ADAPTERS.get(schema)
    if adapter is None:
        raise PluginValidationError(f"Unsupported Agent Plugins schema: {schema}")
    metadata, diagnostics = adapter.parse_metadata(raw)
    package = PluginPackage(
        schema=schema,
        metadata=metadata,
        components=adapter.discover_components(root),
        manifest=PLUGIN_MANIFEST_RESOURCE,
        manifest_revision=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        diagnostics=diagnostics,
    )
    return ResolvedPlugin(root=root, package=package)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PluginValidationError(f"Duplicate key in plugin.json: {key}")
        value[key] = item
    return value


__all__ = ["MAX_PLUGIN_MANIFEST_BYTES", "resolve_plugin"]
